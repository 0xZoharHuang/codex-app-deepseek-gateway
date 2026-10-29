from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from codex_app_deepseek import lifecycle as lc


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def build_fixture_wheel(path):
    name = "codex_app_deepseek_gateway-0.0.0.dist-info"
    files = {
        "codex_app_deepseek/__init__.py": '__version__ = "0.0.0"\n',
        name + "/METADATA": "Metadata-Version: 2.1\nName: codex-app-deepseek-gateway\nVersion: 0.0.0\n",
        name + "/WHEEL": "Wheel-Version: 1.0\nGenerator: lifecycle-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        name + "/entry_points.txt": "[console_scripts]\ncodex-app-deepseek = codex_app_deepseek.cli:main\n",
    }
    for module in ("lifecycle.py", "cli.py", "credentials.py", "app_config.py", "gateway_policy.py", "catalog.py", "paths.py", "atomic_io.py", "native_cleanup.py"):
        files["codex_app_deepseek/" + module] = (Path(lc.__file__).parent / module).read_text(encoding="utf-8")
    # Carry the real ACL helper and its parser dependency in this synthetic wheel.
    # A stub here would hide failures in the native Windows uninstall path.
    import tomlkit
    parser_root = Path(tomlkit.__file__).parent
    for dependency in parser_root.rglob("*.py"):
        files["tomlkit/" + dependency.relative_to(parser_root).as_posix()] = dependency.read_text(encoding="utf-8")
    files[name + "/RECORD"] = "".join(key + ",,\n" for key in [*files, name + "/RECORD"])
    with zipfile.ZipFile(path, "w") as archive:
        for key, value in files.items(): archive.writestr(key, value)


@pytest.fixture
def bundle(tmp_path):
    wheels = tmp_path / "offline \u79bb\u7ebf wheels"; wheels.mkdir()
    wheel = wheels / "codex_app_deepseek_gateway-0.0.0-py3-none-any.whl"
    build_fixture_wheel(wheel)
    manifest = tmp_path / "release.json"
    release = {"schema_version": 1, "project": lc.PROJECT, "version": "0.0.0",
               "source_repository": lc.SOURCE_REPOSITORY, "source_commit": "a" * 40,
               "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
               "gateway_wheel": wheel.name, "files": [{"filename": wheel.name, "sha256": lc.digest(wheel)}],
               "app_baseline": {"app_version": "26.1016.8120.0", "native_version": "0.163.0-alpha.11", "native_sha256": "b" * 64}}
    save(manifest, release)
    python = tmp_path / "independent python.exe"; python.write_bytes(b"fixture executable; never executed")
    return SimpleNamespace(root=tmp_path, wheels=wheels, wheel=wheel, manifest=manifest, release=release,
                           python=python, install=tmp_path / "managed install", data=tmp_path / "persistent data")


class FakeRunner:
    def __init__(self): self.calls=[]; self.fail_pip=False
    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if "import sys,json,platform" in argv[-1]:
            return SimpleNamespace(stdout=json.dumps({"python": f"{sys.version_info.major}.{sys.version_info.minor}", "platform": "win32", "bits": "64bit", "is_venv": False}))
        if "venv" in argv:
            venv = Path(argv[-1]); (venv / "Scripts").mkdir(parents=True)
            (venv / "Scripts/python.exe").write_bytes(b"owned fixture python")
            (venv / "pyvenv.cfg").write_text("home = " + str(Path(argv[0]).parent) + "\nexecutable = " + str(argv[0]) + "\ninclude-system-site-packages = false\n", encoding="utf-8")
        if "pip" in argv and "install" in argv:
            assert {"--no-index", "--no-deps", "--require-hashes", "--no-cache-dir"} <= set(argv)
            if self.fail_pip: raise lc.LifecycleError("synthetic installation failure")
        if any("ScriptMaker" in value for value in argv):
            target = Path(argv[-1]); target.mkdir(parents=True, exist_ok=True)
            command = target / "codex-app-deepseek.exe"
            command.write_bytes(b"MZ fixture standard launcher; never executed\n#!" + str(target / "python.exe").encode() + b" -I\n")
            with zipfile.ZipFile(command, "a") as archive:
                archive.writestr("__main__.py", "from codex_app_deepseek.cli import main\n")
        if "import importlib.metadata" in argv[-1] or argv[-1] == "--version": return SimpleNamespace(stdout="0.0.0\n")
        return SimpleNamespace(stdout="")


def installed(bundle, runner=None):
    runner = runner or FakeRunner()
    result = lc.install(bundle.manifest, lc.digest(bundle.manifest), bundle.wheels, bundle.python,
                        bundle.install, bundle.data, run=runner)
    return result, runner


def activate(bundle):
    result, runner = installed(bundle)
    lc.activate(bundle.install, result["release_id"])
    return result, runner


def test_complete_manifest_hash_checks_happen_before_any_execution(bundle):
    bundle.wheel.write_bytes(b"tampered")
    runner=FakeRunner()
    with pytest.raises(lc.LifecycleError, match="SHA256"):
        installed(bundle, runner)
    assert runner.calls == [] and not bundle.install.exists() and not bundle.data.exists()


@pytest.mark.parametrize("change", [
    lambda x: x.update(source_repository="https://untrusted.invalid/repo"),
    lambda x: x.update(source_commit="main"),
    lambda x: x.update(extra_field=True),
    lambda x: x.update(gateway_wheel="../outside.whl"),
    lambda x: x["files"].append(dict(x["files"][0])),
    lambda x: x["files"][0].update(filename="../outside.whl"),
    lambda x: x["files"][0].update(sha256="missing"),
    lambda x: x["app_baseline"].update(native_sha256="missing"),
])
def test_invalid_release_never_executes_or_mutates_home(bundle, change):
    change(bundle.release); save(bundle.manifest,bundle.release)
    runner=FakeRunner()
    with pytest.raises(lc.LifecycleError): installed(bundle, runner)
    assert runner.calls == [] and not bundle.install.exists() and not bundle.data.exists()


def test_offline_install_does_not_activate_or_touch_user_data(bundle):
    bundle.data.mkdir(); (bundle.data / "session.keep").write_bytes(b"user session")
    result, runner = installed(bundle)
    assert result["status"] == "installed_not_activated"
    assert not (bundle.install / "active.json").exists()
    assert (bundle.data / "session.keep").read_bytes() == b"user session"
    directory=Path(result["release_dir"])
    receipt=lc._json(directory / "installation.json")
    assert lc.digest(Path(receipt["bootstrap_wheel"])) == receipt["bootstrap_sha256"]
    assert "--hash=sha256:" in (directory / "requirements.lock").read_text()
    assert any(call[0][-1] == "check" for call in runner.calls)
    again,_=installed(bundle)
    assert again["status"] == "already_installed"


def test_failed_offline_install_never_activates_and_removes_only_own_partial_release(bundle):
    bundle.data.mkdir(); user=bundle.data / "user.keep"; user.write_bytes(b"keep")
    runner=FakeRunner(); runner.fail_pip=True
    with pytest.raises(lc.LifecycleError): installed(bundle,runner)
    assert not (bundle.install / "active.json").exists()
    assert list((bundle.install / "releases").iterdir()) == []
    assert user.read_bytes() == b"keep"


def test_version_combinations_require_explicit_revalidation(bundle):
    result,_=activate(bundle)
    match=lc.diagnose(bundle.install,observed_baseline=bundle.release["app_baseline"])
    assert match["status"] == "compatible" and match["credentials_read"] is False
    changed={**bundle.release["app_baseline"],"native_sha256":"c"*64}
    verdict=lc.diagnose(bundle.install,observed_baseline=changed)
    assert verdict["status"] == "compatibility_revalidation_required"
    assert verdict["mismatched_fields"] == ["native_sha256"]
    with pytest.raises(lc.LifecycleError,match="revalidation"):
        lc.start(bundle.install,observed_baseline=changed)


def test_activation_does_not_take_over_existing_service(bundle):
    result,_=installed(bundle)
    bundle.data.mkdir(); save(bundle.data / "service.json",{"pid":123,"created":456})
    with pytest.raises(lc.LifecycleError,match="reconcile"):
        lc.activate(bundle.install,result["release_id"])
    assert not (bundle.install / "active.json").exists()


class FakeProcess:
    def __init__(self, directory, data):
        self.pid=123; self.created=12345.0; self.image=str(directory / "venv/Scripts/python.exe")
        import psutil
        self.owner=psutil.Process().username(); self.arguments=[self.image,"-I","-m","codex_app_deepseek.cli","serve","--home",str(data),"--port","42817"]
        self.terminated=False
    def create_time(self): return self.created
    def exe(self): return self.image
    def username(self): return self.owner
    def cmdline(self): return self.arguments
    def terminate(self): self.terminated=True
    def wait(self,timeout): return 0


def process_fixture(bundle):
    result,_=activate(bundle)
    directory=Path(result["release_dir"])
    bundle.data.mkdir(exist_ok=True)
    process=FakeProcess(directory,bundle.data)
    save(bundle.data / "service.json",{"pid":123,"created":process.created,"executable":process.image,"port":42817})
    def factory(pid):
        return SimpleNamespace(username=lambda:__import__("psutil").Process().username()) if pid==os.getpid() else process
    return process,factory


@pytest.mark.parametrize("mismatch", ["created","executable","user","command","home"])
def test_stop_refuses_wrong_identity_without_terminating_any_process(bundle,mismatch):
    process,factory=process_fixture(bundle)
    if mismatch=="created": process.created+=100
    if mismatch=="executable": process.image=str(bundle.root / "another.exe")
    if mismatch=="user": process.owner="EXAMPLE\\unrelated-fixture-user"
    if mismatch=="command": process.arguments=[process.image,"unrelated"]
    if mismatch=="home": process.arguments[6]=str(bundle.root / "other-home")
    with pytest.raises(lc.LifecycleError): lc.stop(bundle.install,process_factory=factory,remove_task=False)
    assert not process.terminated and (bundle.data / "service.json").exists()


def test_stop_uses_only_matching_pid_creation_image_owner_and_command(bundle):
    process,factory=process_fixture(bundle)
    assert lc.stop(bundle.install,process_factory=factory,remove_task=False)["status"]=="stopped"
    assert process.terminated and not (bundle.data / "service.json").exists()


def test_record_replacement_is_not_deleted_by_stop(bundle):
    process,factory=process_fixture(bundle)
    def terminate():
        process.terminated=True
        save(bundle.data / "service.json",{"pid":999,"created":999,"executable":"other"})
    process.terminate=terminate
    with pytest.raises(lc.LifecycleError,match="record changed"):
        lc.stop(bundle.install,process_factory=factory,remove_task=False)
    assert lc._json(bundle.data / "service.json")["pid"]==999


def checkpoint(bundle):
    from codex_app_deepseek.app_config import prepare,apply,PINNED_MODEL
    activate(bundle)
    bundle.data.mkdir(exist_ok=True)
    config=bundle.root / "fixture App" / "config.toml"; config.parent.mkdir()
    original=b'# user-owned config\ncustom_setting="retain"\n'; config.write_bytes(original)
    catalog=bundle.root / "catalog.json"; save(catalog,{"models":[{"slug":PINNED_MODEL}]})
    helper=bundle.root / "auth.exe"; helper.write_bytes(b"never executed")
    record=apply(prepare(config,catalog_path=catalog,auth_command=helper,port=42817),backup_dir=bundle.data / "config-backups")
    lc.register_checkpoint(bundle.install,record.manifest_path)
    return config,original,record


def test_rollback_restores_only_registered_project_checkpoint(bundle):
    config,original,record=checkpoint(bundle)
    unrelated=bundle.data / "sessions" / "unrelated.json"; unrelated.parent.mkdir(); unrelated.write_bytes(b"keep")
    result=lc.rollback(bundle.install)
    assert result["status"]=="rolled_back" and config.read_bytes()==original
    assert unrelated.read_bytes()==b"keep"
    assert lc.rollback(bundle.install)["restored_checkpoints"]==[]


def test_changed_registry_checkpoint_refuses_without_touching_app(bundle):
    config,_,record=checkpoint(bundle)
    before=config.read_bytes()
    record.manifest_path.write_bytes(record.manifest_path.read_bytes()+b"\n")
    with pytest.raises(lc.LifecycleError,match="checkpoint changed"):
        lc.rollback(bundle.install)
    assert config.read_bytes()==before


def test_restore_conflict_keeps_service_and_installed_files(bundle,monkeypatch):
    config,_,record=checkpoint(bundle)
    from codex_app_deepseek import app_config
    monkeypatch.setattr(app_config,"restore",lambda path:(_ for _ in ()).throw(ValueError("synthetic owned-field conflict")))
    with pytest.raises(ValueError,match="conflict"):
        lc.prepare_uninstall(bundle.install,remove_task=False)
    assert (bundle.install / "active.json").exists()
    assert not (bundle.install / "uninstall-ready.json").exists()


def test_external_uninstall_preserves_data_project_and_recovery_bootstrap(bundle):
    result,_=activate(bundle)
    bundle.data.mkdir(exist_ok=True); session=bundle.data / "session.json";session.write_bytes(b"user session")
    project=bundle.root / "user-project.txt";project.write_bytes(b"user work")
    prepared=lc.prepare_uninstall(bundle.install,remove_task=False)
    assert prepared["status"]=="ready_for_external_cleanup"
    assert Path(result["release_dir"]).exists()
    finished=lc.finalize_uninstall(bundle.install,prepared["receipt_sha256"])
    assert finished["status"]=="uninstalled_active_release"
    assert not Path(result["release_dir"]).exists() and not (bundle.install / "active.json").exists()
    assert session.read_bytes()==b"user session" and project.read_bytes()==b"user work"
    assert list((bundle.install / "bootstrap").glob("*.whl"))


def test_unknown_release_files_prevent_uninstall(bundle):
    result,_=activate(bundle)
    note=Path(result["release_dir"]) / "my-notes.txt";note.write_bytes(b"user changes")
    with pytest.raises(lc.LifecycleError,match="unknown files"):
        lc.prepare_uninstall(bundle.install,remove_task=False)
    assert note.read_bytes()==b"user changes" and (bundle.install / "active.json").exists()


def test_final_deletion_refuses_the_interpreter_being_removed(bundle,monkeypatch):
    result,_=activate(bundle)
    prepared=lc.prepare_uninstall(bundle.install,remove_task=False)
    monkeypatch.setattr(sys,"executable",str(Path(result["release_dir"]) / "venv/Scripts/python.exe"))
    with pytest.raises(lc.LifecycleError,match="independent Python"):
        lc.finalize_uninstall(bundle.install,prepared["receipt_sha256"])
    assert Path(result["release_dir"]).exists()


def test_cleanup_failure_has_explicit_retry_receipt(bundle,monkeypatch):
    result,_=activate(bundle)
    prepared=lc.prepare_uninstall(bundle.install,remove_task=False)
    original=lc.shutil.rmtree
    monkeypatch.setattr(lc.shutil,"rmtree",lambda path:(_ for _ in ()).throw(PermissionError("fixture lock")))
    pending=lc.finalize_uninstall(bundle.install,prepared["receipt_sha256"])
    assert pending["status"]=="uninstalled_cleanup_pending"
    assert not (bundle.install / "active.json").exists()
    monkeypatch.setattr(lc.shutil,"rmtree",original)
    assert lc.finalize_uninstall(bundle.install,pending["receipt_sha256"])["status"]=="uninstalled_active_release"


def test_health_ignores_proxy_environment_and_never_calls_a_model(monkeypatch):
    captured={}
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):return b'{"status":"ready","model":"fixture","accepted_models":["fixture"],"allowed_providers":["deepseek"],"config_sha256":null}'
    class Opener:
        def open(self,request,timeout):
            captured["url"]=request.full_url;captured["timeout"]=timeout
            return Response()
    def build(proxy):
        assert proxy.proxies=={};return Opener()
    monkeypatch.setattr(lc.urllib.request,"build_opener",build)
    result=lc.health(42817,token_provider=lambda:"synthetic-token")
    assert captured["url"]=="http://127.0.0.1:42817/healthz"
    assert result["upstream_model_request_performed"] is False


@pytest.mark.skipif(sys.platform!="win32",reason="Native Windows offline installer fixture")
def test_native_independent_python_installs_real_offline_fixture_wheel(bundle):
    # This package intentionally has no model/runtime code or dependencies. The test
    # exercises actual venv/pip/hash/check/version plumbing entirely in the fixture.
    independent=Path(getattr(sys,"_base_executable",sys.executable))
    result=lc.install(bundle.manifest,lc.digest(bundle.manifest),bundle.wheels,independent,bundle.install,bundle.data)
    assert result["status"]=="installed_not_activated"
    receipt=lc._json(Path(result["release_dir"])/"installation.json")
    assert receipt["version"]=="0.0.0"
    assert not bundle.data.exists() and not (bundle.install/"active.json").exists()
    directory = Path(result["release_dir"])
    command = directory / lc._ENTRYPOINT_PATH
    assert receipt["entrypoint"] == lc._entrypoint_proof(directory)
    shadow = bundle.root / "harmless shadow"; package = shadow / "codex_app_deepseek"; package.mkdir(parents=True)
    marker = shadow / "SHADOW_EXECUTED"
    (package / "__init__.py").write_text('__version__ = "FOREIGN-SHADOW"\n')
    (package / "cli.py").write_text('from pathlib import Path\ndef main():\n Path('+repr(str(marker))+').write_text("shadow")\n return 91\n')
    environment = dict(os.environ)
    environment.update(PYTHONPATH=str(shadow), PYTHONHOME=str(shadow / "missing home"),
                       PYTHONSTARTUP=str(shadow / "startup.py"))
    # Exercise the actual public exe directly, bypassing lifecycle._run.
    direct = subprocess.run([str(command), "--version"], cwd=shadow, env=environment,
                            capture_output=True, text=True, timeout=15)
    assert direct.returncode == 0 and direct.stdout.strip() == "0.0.0", direct.stderr
    assert not marker.exists()


@pytest.mark.skipif(sys.platform!="win32",reason="Native Windows PowerShell installer fixture")
def test_native_powershell_install_activate_and_external_uninstall(bundle):
    independent=Path(getattr(sys,"_base_executable",sys.executable))
    # The external installer is a repository/release artifact, not a wheel file.
    script=Path(__file__).resolve().parents[1] / "scripts/install_gateway.ps1"
    powershell=Path(os.environ.get("SystemRoot","C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    common=[str(powershell),"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",str(script)]
    install=subprocess.run([*common,"-Action","Install","-ManifestPath",str(bundle.manifest),"-ManifestSha256",lc.digest(bundle.manifest),
                            "-Wheelhouse",str(bundle.wheels),"-PythonExe",str(independent),"-InstallRoot",str(bundle.install),"-DataHome",str(bundle.data)],
                           capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=90)
    assert install.returncode==0, install.stderr
    installed=json.loads(install.stdout)
    assert installed["status"]=="installed_not_activated"
    activate=subprocess.run([*common,"-Action","Activate","-InstallRoot",str(bundle.install),"-ReleaseId",installed["release_id"]],
                            capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=30)
    assert activate.returncode==0, activate.stderr
    bundle.data.mkdir(exist_ok=True); session=bundle.data/"user-session.keep"; session.write_bytes(b"preserve this session")
    uninstall=subprocess.run([*common,"-Action","Uninstall","-InstallRoot",str(bundle.install)],
                             capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=30)
    assert uninstall.returncode==0, uninstall.stderr
    assert json.loads(uninstall.stdout)["status"]=="uninstalled_active_release"
    assert not Path(installed["release_dir"]).exists()
    assert session.read_bytes()==b"preserve this session"


def test_listener_must_belong_to_owned_pid_before_health_token_read(bundle,monkeypatch):
    process,_=process_fixture(bundle)
    process.net_connections=lambda kind:[]
    monkeypatch.setattr(lc,"health",lambda port:pytest.fail("Wrong listener must not read a health credential"))
    with pytest.raises(lc.LifecycleError,match="listener"):
        lc._owned_health(process,bundle.data,42817)


def test_matching_owned_listener_can_probe_local_health(bundle,monkeypatch):
    process,_=process_fixture(bundle)
    process.net_connections=lambda kind:[SimpleNamespace(status="LISTEN",laddr=SimpleNamespace(ip="127.0.0.1",port=42817))]
    monkeypatch.setattr(lc,"health",lambda port:{"status":"ready","port":port})
    assert lc._owned_health(process,bundle.data,42817)=={"status":"ready","port":42817}


def test_public_restore_is_reconciled_by_lifecycle_rollback(bundle):
    from codex_app_deepseek import app_config as ac
    config, original, record = checkpoint(bundle)
    ac.restore(record.manifest_path)
    result = lc.rollback(bundle.install)
    assert result['restored_checkpoints'] == []
    assert result['reconciled_completed_restores'] is True
    assert config.read_bytes() == original
    registry = lc._json(bundle.install / 'checkpoints.json')
    assert registry['schema_version'] == 2
    assert registry['checkpoints'][0]['state'] == 'restored'
    assert registry['checkpoints'][0]['sha256'] == lc.digest(record.manifest_path)


def test_public_restore_does_not_block_prepare_uninstall(bundle):
    from codex_app_deepseek import app_config as ac
    config, original, record = checkpoint(bundle)
    ac.restore(record.manifest_path)
    result = lc.prepare_uninstall(bundle.install, remove_task=False)
    assert result['status'] == 'ready_for_external_cleanup'
    assert config.read_bytes() == original
    assert (bundle.install / 'uninstall-ready.json').exists()


def test_restore_success_registry_write_failure_recovers_without_replay(bundle, monkeypatch):
    from codex_app_deepseek import app_config as ac
    config, original, record = checkpoint(bundle)
    original_registry = (bundle.install / 'checkpoints.json').read_bytes()
    writer, restorer, calls = lc._write_registry, ac.restore, []
    def count_restore(path):
        calls.append(path)
        return restorer(path)
    def failed_writer(*args):
        raise OSError('fixture write failed')
    monkeypatch.setattr(ac, 'restore', count_restore)
    monkeypatch.setattr(lc, '_write_registry', failed_writer)
    with pytest.raises(lc.LifecycleError, match='after 1 completed'):
        lc.rollback(bundle.install)
    assert len(calls) == 1 and config.read_bytes() == original
    assert (bundle.install / 'checkpoints.json').read_bytes() == original_registry
    assert lc._json(record.manifest_path)['state'] == 'restored'
    monkeypatch.setattr(lc, '_write_registry', writer)
    result = lc.rollback(bundle.install)
    assert result['reconciled_completed_restores'] and result['restored_checkpoints'] == []
    assert len(calls) == 1


def test_false_restored_label_does_not_pass_current_hash_alone(bundle):
    config, _, record = checkpoint(bundle)
    data = lc._json(record.manifest_path)
    data.update(state='restored', restored_sha256=lc.digest(config), restored_at='fixture')
    save(record.manifest_path, data)
    before = config.read_bytes()
    with pytest.raises(lc.LifecycleError, match='owned settings remain applied'):
        lc.rollback(bundle.install)
    assert config.read_bytes() == before


@pytest.mark.parametrize('field,value', [('created_at', 'altered'), ('schema_version', True)])
def test_immutable_checkpoint_fields_cannot_be_reanchored(bundle, field, value):
    from codex_app_deepseek import app_config as ac
    config, original, record = checkpoint(bundle)
    ac.restore(record.manifest_path)
    data = lc._json(record.manifest_path); data[field] = value; save(record.manifest_path, data)
    with pytest.raises((lc.LifecycleError, ac.ConfigurationError)):
        lc.rollback(bundle.install)
    assert config.read_bytes() == original


def legacy_registry(bundle):
    path = bundle.install / 'checkpoints.json'
    data = lc._json(path)
    save(path, {'project': lc.PROJECT, 'checkpoints': [{k: item[k] for k in ('path', 'sha256', 'state')}
        for item in data['checkpoints']]})


def test_unchanged_legacy_registry_can_establish_verified_immutable_anchors(bundle):
    config, original, record = checkpoint(bundle)
    legacy_registry(bundle)
    result = lc.rollback(bundle.install)
    assert config.read_bytes() == original
    assert lc._json(bundle.install / 'checkpoints.json')['schema_version'] == 2
    assert result['reconciled_completed_restores']


def test_changed_legacy_registry_never_guesses_old_immutable_snapshot(bundle):
    from codex_app_deepseek import app_config as ac
    config, original, record = checkpoint(bundle)
    legacy_registry(bundle)
    anchor = (bundle.install / 'checkpoints.json').read_bytes()
    ac.restore(record.manifest_path)
    with pytest.raises(lc.LifecycleError, match='cannot be reconstructed'):
        lc.rollback(bundle.install)
    assert config.read_bytes() == original
    assert (bundle.install / 'checkpoints.json').read_bytes() == anchor


def test_batch_preflight_stops_older_conflict_before_any_config_restore(bundle):
    from codex_app_deepseek import app_config as ac
    config, _, first = checkpoint(bundle)
    config.write_text(config.read_text(encoding='utf8').replace('web_search = "live"', 'web_search = "cached"'), encoding='utf8')
    second = ac.apply(ac.prepare(config, catalog_path=bundle.root/'catalog.json', auth_command=bundle.root/'auth.exe', port=42817), backup_dir=bundle.data/'config-backups')
    lc.register_checkpoint(bundle.install, second.manifest_path)
    before = config.read_bytes()
    manifests = {p: p.read_bytes() for p in [first.manifest_path, second.manifest_path, bundle.install/'checkpoints.json']}
    with pytest.raises(ac.ConfigurationConflict):
        lc.rollback(bundle.install)
    assert config.read_bytes() == before
    assert all(p.read_bytes() == raw for p, raw in manifests.items())


def test_batch_reverse_chain_restores_multiple_versions_and_preserves_new_empty_table(bundle):
    from codex_app_deepseek import app_config as ac
    import tomllib
    config, _, first = checkpoint(bundle)
    second = ac.apply(ac.prepare(config, catalog_path=bundle.root/'catalog.json', auth_command=bundle.root/'auth.exe', port=42818), backup_dir=bundle.data/'config-backups')
    lc.register_checkpoint(bundle.install, second.manifest_path)
    config.write_bytes(config.read_bytes()+b'\n[later_empty]\n')
    result = lc.rollback(bundle.install)
    assert result['restored_checkpoints'] == [str(second.manifest_path), str(first.manifest_path)]
    assert tomllib.loads(config.read_text(encoding='utf8')) == {'custom_setting':'retain', 'later_empty':{}}
    assert lc.rollback(bundle.install)['restored_checkpoints'] == []


def test_known_restored_checkpoint_preserves_later_user_configuration(bundle):
    config, _, record = checkpoint(bundle)
    lc.rollback(bundle.install)
    config.write_bytes(config.read_bytes()+b'\nmodel = "later-user-model"\n')
    before = config.read_bytes()
    assert lc.rollback(bundle.install)['restored_checkpoints'] == []
    assert config.read_bytes() == before


def test_legacy_restore_without_input_output_evidence_with_later_edits_requires_proof(bundle):
    from codex_app_deepseek import app_config as ac
    config, _, record = checkpoint(bundle)
    ac.restore(record.manifest_path)
    manifest=lc._json(record.manifest_path)
    for name in ('restore_input_file','restore_input_sha256','restored_file','restore_prepared_at'):
        manifest.pop(name)
    save(record.manifest_path,manifest)
    config.write_bytes(config.read_bytes()+b'\nnew_user_setting = true\n')
    before = config.read_bytes()
    with pytest.raises(lc.LifecycleError, match='does not match the current config'):
        lc.rollback(bundle.install)
    assert config.read_bytes() == before


def test_registered_checkpoint_path_and_duplicate_entries_are_validated(bundle):
    config, _, record = checkpoint(bundle)
    registry_path = bundle.install/'checkpoints.json'
    data = lc._json(registry_path)
    data['checkpoints'].append(dict(data['checkpoints'][0]));save(registry_path,data)
    before = config.read_bytes()
    with pytest.raises(lc.LifecycleError, match='Duplicate'):
        lc.rollback(bundle.install)
    assert config.read_bytes() == before


def test_multiple_public_restores_can_reconcile_after_current_passes_intermediate_states(bundle):
    from codex_app_deepseek import app_config as ac
    import tomllib
    config, _, first = checkpoint(bundle)
    second = ac.apply(ac.prepare(config, catalog_path=bundle.root/'catalog.json', auth_command=bundle.root/'auth.exe', port=42818), backup_dir=bundle.data/'config-backups')
    lc.register_checkpoint(bundle.install, second.manifest_path)
    config.write_bytes(config.read_bytes()+b'\n[unrelated_later]\n')
    ac.restore(second.manifest_path)
    intermediate = lc.digest(config)
    ac.restore(first.manifest_path)
    assert lc.digest(config) != intermediate
    config.write_bytes(config.read_bytes()+b'\n# still newer comment\n')
    before = config.read_bytes()
    result = lc.rollback(bundle.install)
    assert result['reconciled_completed_restores'] and result['restored_checkpoints'] == []
    assert config.read_bytes() == before
    assert tomllib.loads(before.decode()) == {'custom_setting':'retain', 'unrelated_later':{}}


@pytest.mark.parametrize('failure_point', ['before_config_write', 'after_config_write'])
def test_prepared_restore_resumes_exact_captured_transition(bundle, monkeypatch, failure_point):
    from codex_app_deepseek import app_config as ac
    config, original, record = checkpoint(bundle)
    original_atomic, original_manifest = ac._atomic_write, ac._write_manifest
    config_writes = []
    def atomic(path, raw, **kwargs):
        if path == config:
            config_writes.append(raw)
            if failure_point == 'before_config_write':
                raise OSError('fixture crash before config write')
        return original_atomic(path, raw, **kwargs)
    def manifest(path, data):
        if data['state'] == 'restored' and failure_point == 'after_config_write':
            raise OSError('fixture crash after config write')
        return original_manifest(path, data)
    monkeypatch.setattr(ac, '_atomic_write', atomic)
    monkeypatch.setattr(ac, '_write_manifest', manifest)
    with pytest.raises(ac.ConfigurationError):
        ac.restore(record.manifest_path)
    prepared_manifest = lc._json(record.manifest_path)
    assert prepared_manifest['state'] == 'restore_prepared'
    assert prepared_manifest['restore_input_file'] == 'restore-input.toml'
    assert prepared_manifest['restored_file'] == 'restored.toml'
    assert config.read_bytes() == (original if failure_point == 'after_config_write' else
                                  (record.manifest_path.parent/'restore-input.toml').read_bytes())
    successful_writes = []
    def successful_atomic(path, raw, **kwargs):
        if path == config: successful_writes.append(raw)
        return original_atomic(path, raw, **kwargs)
    monkeypatch.setattr(ac, '_atomic_write', successful_atomic)
    monkeypatch.setattr(ac, '_write_manifest', original_manifest)
    result = lc.rollback(bundle.install)
    assert result['status'] == 'rolled_back'
    assert config.read_bytes() == original
    assert len(successful_writes) == (1 if failure_point == 'before_config_write' else 0)
    assert lc._json(record.manifest_path)['state'] == 'restored'


def test_restore_proof_cannot_drop_unowned_input_content_even_with_matching_result_hash(bundle):
    from codex_app_deepseek import app_config as ac
    config, original, record = checkpoint(bundle)
    config.write_bytes(config.read_bytes()+b'\n[real_user_table]\nkeep="yes"\n')
    ac.restore(record.manifest_path)
    correct = config.read_bytes()
    manifest = lc._json(record.manifest_path)
    # Forge a weaker output and its hash. C is the actual pre-restore source and
    # the deterministic B/A/C -> R check must still catch the deleted user table.
    (record.manifest_path.parent/'restored.toml').write_bytes(original)
    manifest['restored_sha256'] = hashlib.sha256(original).hexdigest()
    save(record.manifest_path, manifest)
    with pytest.raises(ac.ConfigurationError, match='does not reproduce'):
        lc.rollback(bundle.install)
    assert config.read_bytes() == correct


def test_prepared_restore_with_new_user_edit_is_not_recomputed_or_overwritten(bundle, monkeypatch):
    from codex_app_deepseek import app_config as ac
    config, _, record = checkpoint(bundle)
    original_atomic = ac._atomic_write
    def failing(path, raw, **kwargs):
        if path == config: raise OSError('fixture failure')
        return original_atomic(path, raw, **kwargs)
    monkeypatch.setattr(ac, '_atomic_write', failing)
    with pytest.raises(ac.ConfigurationError): ac.restore(record.manifest_path)
    monkeypatch.setattr(ac, '_atomic_write', original_atomic)
    config.write_bytes(config.read_bytes()+b'\n# later independent edit\n')
    before = config.read_bytes()
    with pytest.raises(lc.LifecycleError, match='after captured restore preparation'):
        lc.rollback(bundle.install)
    assert config.read_bytes() == before


def test_restore_input_and_output_snapshots_are_private_and_byte_exact(bundle):
    from codex_app_deepseek import app_config as ac
    config, original, record = checkpoint(bundle)
    actual_input = config.read_bytes() + b'\n# user later content\n'
    config.write_bytes(actual_input)
    ac.restore(record.manifest_path)
    manifest = lc._json(record.manifest_path)
    assert (record.manifest_path.parent/'restore-input.toml').read_bytes() == actual_input
    assert (record.manifest_path.parent/'restored.toml').read_bytes() == config.read_bytes()
    assert manifest['restore_input_sha256'] == hashlib.sha256(actual_input).hexdigest()
    assert manifest['restored_sha256'] == lc.digest(config)
    assert ac._restore_evidence(record.manifest_path, manifest, original,
        (record.manifest_path.parent/'applied.toml').read_bytes()) == (actual_input, config.read_bytes())

def test_stop_excludes_late_service_record_replacement_after_hash_check(bundle, monkeypatch):
    from codex_app_deepseek import cli
    process, factory = process_fixture(bundle)
    record_path = bundle.data / "service.json"
    real_digest = lc.digest
    attempted = []

    def checked_digest(path):
        value = real_digest(path)
        # First read is _owned_process; the second is the final compare before unlink.
        if Path(path) == record_path and process.terminated:
            with pytest.raises(cli.ServiceConfigError, match="already owns"):
                with cli._service_lease(bundle.data):
                    cli._write_service_record(bundle.data, {"pid": 999, "created": 999})
            attempted.append(True)
        return value

    monkeypatch.setattr(lc, "digest", checked_digest)
    result = lc.stop(bundle.install, process_factory=factory, remove_task=False)
    assert result["status"] == "stopped"
    assert attempted == [True]
    assert not record_path.exists()


def test_stop_preserves_new_live_home_owner_and_never_removes_its_task(bundle, monkeypatch):
    from contextlib import ExitStack
    from codex_app_deepseek import cli
    process, factory = process_fixture(bundle)
    new_record = {"pid": 999, "created": 999, "executable": "synthetic new owner"}
    with ExitStack() as resources:
        def wait(timeout):
            # The old process has ended; a new service wins the home lease.
            resources.enter_context(cli._service_lease(bundle.data))
            cli._write_service_record(bundle.data, new_record)
            return 0
        process.wait = wait
        monkeypatch.setattr(lc, "_task", lambda *a, **k: pytest.fail("Do not remove a new owner's task"))
        with pytest.raises(lc.LifecycleError, match="another gateway"):
            lc.stop(bundle.install, process_factory=factory)
        assert process.terminated
        assert lc._json(bundle.data / "service.json") == new_record


def test_stop_keeps_home_lease_until_own_task_removal_finishes(bundle, monkeypatch):
    from codex_app_deepseek import cli
    process, factory = process_fixture(bundle)
    removals = []
    def remove_task(root, directory, data, port, operation):
        assert operation == "remove" and process.terminated
        with pytest.raises(cli.ServiceConfigError, match="already owns"):
            with cli._service_lease(data):
                pytest.fail("Stop must keep the home closed through task removal")
        removals.append(operation)
        return {"removed": True}
    monkeypatch.setattr(lc, "_task", remove_task)
    assert lc.stop(bundle.install, process_factory=factory)["task"] == {"removed": True}
    assert removals == ["remove"]

def test_public_start_cannot_replace_task_while_public_stop_is_removing_it(bundle, monkeypatch):
    process, factory = process_fixture(bundle)
    revision = {"task": "old"}
    operations = []
    def task(root, directory, data, port, operation, at_logon=False):
        operations.append(operation)
        if operation == "start":
            revision["task"] = "new"
            pytest.fail("A concurrent start must not reach task registration")
        with pytest.raises(lc.LifecycleError, match="lifecycle operation"):
            lc.start(bundle.install, at_logon=True, observed_baseline=bundle.release["app_baseline"])
        return {"removed_revision": revision.pop("task")}
    monkeypatch.setattr(lc, "_task", task)
    assert lc.stop(bundle.install, process_factory=factory)["task"]["removed_revision"] == "old"
    assert operations == ["remove"]


def test_public_stop_cannot_remove_task_during_public_start_admission(bundle, monkeypatch):
    activate(bundle)
    bundle.data.mkdir(exist_ok=True)
    class FixtureAdmissionComplete(Exception):
        pass
    def task(root, directory, data, port, operation, at_logon=False):
        assert operation == "start"
        with pytest.raises(lc.LifecycleError, match="lifecycle operation"):
            lc.stop(bundle.install, remove_task=False)
        raise FixtureAdmissionComplete("End fixture before waiting for any real service")
    monkeypatch.setattr(lc, "_task", task)
    with pytest.raises(FixtureAdmissionComplete):
        lc.start(bundle.install, observed_baseline=bundle.release["app_baseline"])


def test_operation_lease_excludes_real_windows_child_then_releases(tmp_path):
    root = tmp_path / "fixture installation"
    root.mkdir()
    code = "from pathlib import Path;import sys;from codex_app_deepseek.lifecycle import _operation_lease,LifecycleError;\ntry:\n with _operation_lease(Path(sys.argv[1])):print('acquired')\nexcept LifecycleError:\n print('locked');sys.exit(9)"
    with lc._operation_lease(root):
        child = subprocess.run([sys.executable, "-I", "-c", code, str(root)], capture_output=True, timeout=10)
        assert child.returncode == 9 and child.stdout.strip() == b"locked"
        # Operation lock and long-lived data-home lease are independent files.
        from codex_app_deepseek import cli
        with cli._service_lease(root):
            assert (root / "lifecycle.lock").exists() and (root / "service.lock").exists()
    child = subprocess.run([sys.executable, "-I", "-c", code, str(root)], capture_output=True, timeout=10)
    assert child.returncode == 0 and child.stdout.strip() == b"acquired"


def test_lease_rejects_unrecognized_lock_name_before_file_creation(tmp_path):
    from codex_app_deepseek import cli
    with pytest.raises(cli.ServiceConfigError, match="lock name"):
        with cli._service_lease(tmp_path, lock_name="../unexpected"):
            pytest.fail("Invalid lock name must not be opened")
    assert not list(tmp_path.iterdir())

@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows runtime path admission")
def test_too_deep_windows_install_is_rejected_before_any_subprocess(bundle):
    install = bundle.install
    suffix = Path("releases") / ("0" * 20) / "venv/Lib/site-packages/codex_app_deepseek_gateway-0.0.0.dist-info/direct_url.json"
    while len(str(install / suffix).encode("utf-16-le")) // 2 <= 259:
        install = install / "nested-fixture-installation"
    bundle.install = install
    def no_execution(*args, **kwargs):
        pytest.fail("Unusable permanent runtime paths must be rejected before venv/pip")
    independent = Path(getattr(sys, "_base_executable", sys.executable))
    with pytest.raises(lc.LifecycleError, match="259 UTF-16"):
        lc.install(bundle.manifest, lc.digest(bundle.manifest), bundle.wheels,
                   independent, bundle.install, bundle.data, run=no_execution)
    assert not bundle.install.exists()
    assert not bundle.data.exists()


def test_observe_signed_package_uses_known_sha_without_executing_native(tmp_path, monkeypatch):
    location = tmp_path / "signed package fixture"
    native = location / "app/resources/codex.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"synthetic native bytes; never executed")
    sha = lc.digest(native)
    monkeypatch.setattr(lc, "KNOWN_OFFICIAL_NATIVE_VERSIONS", {sha: "fixture-native-version"})
    package = {"app_version": "fixture-app-version", "location": str(location),
               "publisher_id": "2p2nqsd0c76g0", "signature_kind": "Store", "status": "Ok"}
    calls = []
    def observer(argv, **kwargs):
        assert Path(argv[0]).name.casefold() == "powershell.exe"
        calls.append(argv)
        return SimpleNamespace(stdout=json.dumps(package))
    result = lc.observe_app(run=observer)
    assert result["native_version"] == "fixture-native-version"
    assert result["native_sha256"] == sha
    assert result["native_version_source"] == "hash-verified-reference"
    assert len(calls) == 1


def test_observe_unknown_sha_is_explicit_and_does_not_guess_native_version(tmp_path, monkeypatch):
    native = tmp_path / "app/resources/codex.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"new unqualified binary; never executed")
    monkeypatch.setattr(lc, "KNOWN_OFFICIAL_NATIVE_VERSIONS", {})
    package = {"app_version": "new-app", "location": str(tmp_path),
               "publisher_id": "2p2nqsd0c76g0", "signature_kind": "Store", "status": "Ok"}
    result = lc.observe_app(run=lambda argv, **kwargs: SimpleNamespace(stdout=json.dumps(package)))
    assert result["native_version"] is None
    assert result["native_sha256"] == lc.digest(native)
    assert result["native_version_source"] == "unrecognized-official-sha256"


@pytest.mark.parametrize("field,value", [("publisher_id", "other"), ("signature_kind", "None"), ("status", "Modified")])
def test_observe_rejects_unverified_package_identity_before_binary_read(tmp_path, monkeypatch, field, value):
    package = {"app_version": "fixture", "location": str(tmp_path),
               "publisher_id": "2p2nqsd0c76g0", "signature_kind": "Store", "status": "Ok"}
    package[field] = value
    monkeypatch.setattr(lc, "digest", lambda path: pytest.fail("Unverified package must not become an observed baseline"))
    with pytest.raises(lc.LifecycleError, match="signed"):
        lc.observe_app(run=lambda argv, **kwargs: SimpleNamespace(stdout=json.dumps(package)))

@pytest.mark.parametrize("member", ["package/CON.txt", "package/bad:.txt", "package/../outside",
    "fixture.data/headers/header.h", "fixture.data/unknown/file"])
def test_windows_wheel_layout_rejects_reserved_or_unmapped_paths_before_execution(bundle, member):
    with zipfile.ZipFile(bundle.wheel, "a") as archive:
        archive.writestr(member, "fixture data")
    bundle.release["files"][0]["sha256"] = lc.digest(bundle.wheel)
    save(bundle.manifest, bundle.release)
    runner = FakeRunner()
    with pytest.raises(lc.LifecycleError, match="Windows installation path|data installation scheme"):
        installed(bundle, runner)
    assert runner.calls == []
    assert not bundle.install.exists()


def test_windows_wheel_data_scripts_use_the_scripts_destination(bundle):
    with zipfile.ZipFile(bundle.wheel, "a") as archive:
        archive.writestr("fixture.data/scripts/fixture-command", "#!python\n")
    bundle.release["files"][0]["sha256"] = lc.digest(bundle.wheel)
    save(bundle.manifest, bundle.release)
    result, _ = installed(bundle)
    assert result["status"] == "installed_not_activated"


def test_activation_initializes_explicit_full_app_policy_without_starting(bundle):
    from codex_app_deepseek import gateway_policy as gp
    result, runner = installed(bundle)
    before_calls = len(runner.calls)
    activated = lc.activate(bundle.install, result["release_id"])
    policy = gp.load_service_config(bundle.data)
    assert set(policy.accepted_models) == set(gp.REQUEST_MODEL_IDS)
    assert set(policy.allowed_providers) == set(gp.DEFAULT_PROVIDERS)
    assert policy.policy_origin == gp.DEFAULT_POLICY_ORIGIN
    assert activated["gateway_policy"]["status"] == "created_fixed_app_policy"
    assert not (bundle.data / "service.json").exists() and len(runner.calls) == before_calls


def test_activation_does_not_overwrite_existing_incomplete_policy_or_change_active(bundle):
    from codex_app_deepseek import gateway_policy as gp
    result, _ = installed(bundle)
    bundle.data.mkdir()
    path = bundle.data / "gateway.json"
    path.write_text(json.dumps({"accepted_models": list(gp.REQUEST_MODEL_IDS),
                                "allowed_providers": ["deepseek"]}) + "\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(lc.LifecycleError, match="required native schema/serial/default routes"):
        lc.activate(bundle.install, result["release_id"])
    assert path.read_bytes() == before and not (bundle.install / "active.json").exists()


def test_owned_lifecycle_commands_ignore_python_environment_without_modifying_parent(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "synthetic-foreign-site")
    monkeypatch.setenv("PYTHONHOME", "synthetic-other-interpreter")
    monkeypatch.setenv("PYTHONSTARTUP", "synthetic-startup.py")
    monkeypatch.setenv("CADSG_FIXTURE_KEEP", "retain")
    captured = []
    def run(argv, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="0.0.0", stderr="")
    monkeypatch.setattr(lc.subprocess, "run", run)
    assert lc._run(["explicit-owned-command.exe", "--version"]).stdout == "0.0.0"
    assert not any(key.upper().startswith("PYTHON") for key in captured[0]["env"])
    assert captured[0]["env"]["CADSG_FIXTURE_KEEP"] == "retain"
    assert os.environ["PYTHONPATH"] == "synthetic-foreign-site"


@pytest.mark.parametrize("mutation", ["argument", "receipt"])
def test_nonisolated_or_false_launcher_proof_is_rejected(bundle, mutation):
    result, _ = installed(bundle)
    directory = Path(result["release_dir"])
    receipt = lc._json(directory / "installation.json")
    if mutation == "argument":
        entry = directory / lc._ENTRYPOINT_PATH
        raw = entry.read_bytes()
        assert b" -I\n" in raw
        entry.write_bytes(raw.replace(b" -I\n", b" -E\n", 1))
    else:
        receipt["entrypoint"]["interpreter_args"] = ["-E"]
    with pytest.raises(lc.LifecycleError, match="launcher"):
        lc._verify_installation(directory, receipt)
