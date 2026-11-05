from __future__ import annotations
import copy, hashlib, json, os, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from codex_app_deepseek import app_delivery as delivery
from codex_app_deepseek import isolated_app as app
from codex_app_deepseek import cli
from codex_app_deepseek import gateway_policy as gp
from test_isolated_app import fixture as native_fixture

@pytest.fixture
def fixture(native_fixture, tmp_path, monkeypatch):
    paths, expected, manifest_path, manifest, request, backend = native_fixture
    # Provider/delivery tests keep native installation mocked; the native helper
    # has its own filesystem/CLI tests and recorded real-CLI integration gate.
    from codex_app_deepseek import native_app_tools as tools
    import shutil
    source = Path(backend.package(expected)["package"]["location"]) / "bundled"
    target = paths["home"] / ".tmp/bundled-marketplaces/openai-bundled"
    target.parent.mkdir(parents=True)
    shutil.copytree(source, target)
    monkeypatch.setattr(tools, "bootstrap", lambda *args, **kwargs: {"state": "configured"})
    monkeypatch.setattr(tools, "configured", lambda *args, **kwargs: {"status": "unit_mock_configuration"})
    root = tmp_path / "install"; root.mkdir()
    directory = root / "releases" / ("a" * 20)
    executable = directory / "venv/Scripts/codex-app-deepseek.exe"
    executable.parent.mkdir(parents=True); executable.write_bytes(b"fixture helper; never executed")
    auth_python=executable.with_name("python.exe");auth_python.write_bytes(b"fixture interpreter; never executed")
    data = tmp_path / "data"; data.mkdir()
    installation = delivery.Installation(root, directory, data, {"release_id": "a" * 20})
    monkeypatch.setattr(delivery, "installed", lambda root: installation)
    actual_launcher = Path(delivery.__file__).parent / "assets/launch_isolated_app.ps1"
    monkeypatch.setattr(delivery, "assets", lambda: (Path(request["baseline"]), actual_launcher))
    catalog = tmp_path / "input-catalog.json"
    catalog.write_text(json.dumps({"models": [{"slug": name} for name in gp.REQUEST_MODEL_IDS]}))
    monkeypatch.setattr(delivery.lc, "register_checkpoint", lambda *args: {"registered": True})
    return SimpleNamespace(paths=paths, expected=expected, manifest=manifest_path, request=request,
                           backend=backend, installation=installation, catalog=catalog,
                           executable=executable, auth_python=auth_python)

def bind(f):
    return delivery.bind(f.installation.root, f.manifest, app.sha(f.manifest), backend=f.backend)

def configure(f):
    bind(f)
    return delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)

def healthy_gateway(f):
    policy = gp.load_service_config(f.installation.data)
    return {"status": "owned_healthy", "port": 42817,
            "health": {"status": "ready", "model": gp.PINNED_MODEL,
                       "accepted_models": list(policy.accepted_models),
                       "allowed_providers": list(policy.allowed_providers),
                       "ordinary_default_provider": policy.ordinary_default_provider,
                       "config_sha256": policy.config_sha256}}

def test_packaged_assets_are_identical_to_the_fixed_sources():
    root = Path(__file__).parents[1]
    baseline, launcher = delivery.assets()
    assert baseline.read_bytes() == (root / "native/app-launch-baseline.json").read_bytes()
    assert launcher.read_bytes() == (root / "scripts/launch_isolated_app.ps1").read_bytes()
    assert app.sha(baseline) == app.BASELINE_SHA256 and app.sha(launcher) == delivery.LAUNCHER_SHA256
    assert b"Invoke-CommandInDesktopPackage" in launcher.read_bytes()

def test_bind_is_explicit_verified_and_never_starts_app(fixture):
    f = fixture; result = bind(f)
    assert result["status"] == "runtime_bound_not_launched"
    assert not f.backend.spawned
    value = delivery.read_binding(f.installation, f.backend)
    assert value["native_manifest"] == str(f.manifest.resolve()) and value["catalog"] is None
    trusted_sha = app.sha(f.manifest)
    f.manifest.write_bytes(b"tampered")
    with pytest.raises(app.LaunchError, match="checksum"):
        delivery.bind(f.installation.root, f.manifest, trusted_sha, backend=f.backend)
    assert not f.backend.spawned

def test_bind_refuses_current_app_before_writing_binding(fixture):
    f = fixture; f.backend.existing = [{"pid": 99}]
    with pytest.raises(delivery.DeliveryError, match="identity"):
        bind(f)
    assert not delivery.binding_path(f.installation).exists()

def test_explicit_bind_can_qualify_same_runtime_for_new_gateway_release(fixture):
    f = fixture; configure(f)
    record = delivery.read_binding(f.installation, f.backend)
    record["gateway_release_id"] = "b" * 20
    app.save(delivery.binding_path(f.installation), record)
    with pytest.raises(delivery.DeliveryError, match="bind again"):
        delivery.read_binding(f.installation, f.backend)
    result = bind(f)
    assert result["gateway_release_id"] == "a" * 20
    assert delivery.read_binding(f.installation, f.backend)["catalog"] is not None

def test_configure_copies_catalog_and_uses_installed_auth_helper(fixture):
    f = fixture; result = configure(f)
    assert result["status"] == "private_app_configured_not_launched"
    value = delivery.read_binding(f.installation, f.backend)
    assert Path(value["catalog"]["path"]).parent == f.installation.data / "app-catalogs"
    assert Path(value["catalog"]["path"]).read_bytes() == f.catalog.read_bytes()
    import tomllib
    config = tomllib.loads((f.paths["home"] / "config.toml").read_text())
    assert config["model_providers"]["codex_app_deepseek"]["auth"]["command"] == str(f.auth_python)
    assert config["model_providers"]["codex_app_deepseek"]["auth"]["args"] == list(delivery.AUTH_ARGS)
    assert config["model_catalog_json"] == value["catalog"]["path"]
    assert Path(result["checkpoint"]).is_file() and not f.backend.spawned
    f.catalog.unlink()  # The input/download is no longer required by App.
    assert delivery.read_binding(f.installation, f.backend)["catalog"]["sha256"] == value["catalog"]["sha256"]

def test_invalid_catalog_is_rejected_before_owned_catalog_or_config_change(fixture):
    f = fixture; bind(f); before = (f.paths["home"] / "config.toml").read_bytes()
    f.catalog.write_text("{}")
    with pytest.raises(ValueError):
        delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    assert not (f.installation.data / "app-catalogs").exists()
    assert (f.paths["home"] / "config.toml").read_bytes() == before

def test_failed_checkpoint_registration_reports_actual_applied_checkpoint(fixture, monkeypatch):
    f = fixture; bind(f)
    def fail(*args): raise OSError("private content must not appear")
    monkeypatch.setattr(delivery.lc, "register_checkpoint", fail)
    with pytest.raises(delivery.DeliveryError, match="Configuration applied but checkpoint registration failed") as caught:
        delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    assert "private content" not in str(caught.value)
    assert delivery.read_binding(f.installation, f.backend)["catalog"] is None
    checkpoints = list((f.installation.data / "config-backups").glob("*/checkpoint.json"))
    assert len(checkpoints) == 1 and str(checkpoints[0]) in str(caught.value)

def test_check_requires_owned_gateway_and_installed_helper(fixture, monkeypatch):
    f = fixture; configure(f)
    monkeypatch.setattr(delivery, "owned_gateway", lambda unused: healthy_gateway(f))
    result = delivery.check(f.installation.root, backend=f.backend)
    assert result["status"] == "validated_not_launched" and not f.backend.spawned
    path = f.paths["home"] / "config.toml"
    path.write_text(path.read_text().replace(str(f.executable).replace("\\", "\\\\"), "D:\\\\unrelated\\\\helper.exe"))
    # TOML may use literal single quotes; use semantic parser roundtrip mutation.
    import tomlkit
    value = tomlkit.parse(path.read_text())
    value["model_providers"]["codex_app_deepseek"]["auth"]["command"] = r"D:\unrelated\helper.exe"
    path.write_text(tomlkit.dumps(value))
    with pytest.raises(delivery.DeliveryError, match="installed helper"):
        delivery.check(f.installation.root, backend=f.backend)

def test_owned_gateway_does_not_accept_an_unowned_listener(fixture, monkeypatch):
    f = fixture
    monkeypatch.setattr(delivery.lc, "_owned_process", lambda *args: (None, None))
    monkeypatch.setattr(delivery.lc, "_owned_health", lambda *args: pytest.fail("No health token read without an owner"))
    with pytest.raises(delivery.DeliveryError, match="not running"):
        delivery.owned_gateway(f.installation)

@pytest.mark.parametrize("returncode,payload,expected", [
    (2, {"status": "observation_timeout", "run_dir": "unused"}, "launch_unverified"),
    (0, {"receipt": {"status": "failed_or_unverified"}}, "launch_unverified"),
])
def test_launch_never_promotes_timeout_or_failure_to_success(fixture, monkeypatch, returncode, payload, expected):
    f = fixture; configure(f)
    monkeypatch.setattr(delivery, "owned_gateway", lambda unused: healthy_gateway(f))
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs)); return SimpleNamespace(returncode=returncode, stdout=json.dumps(payload))
    result = delivery.launch(f.installation.root, backend=f.backend, run=run)
    assert result["status"] == expected and result["exit_code"] == 3 and result["replay_allowed"] is False
    assert len(calls) == 1 and not f.backend.spawned
    args = calls[0][0]
    assert "-BaselinePath" in args and "-NativeManifestSha256" in args
    assert args[args.index("-ExpectedConfigSha256")+1] == app.sha(f.paths["home"] / "config.toml")
    assert args[args.index("-File")+1] == str(delivery.assets()[1])
    assert args[args.index("-PythonPath")+1] == str(f.installation.directory / "venv/Scripts/python.exe")

def test_outer_timeout_is_unknown_and_never_retried(fixture, monkeypatch):
    f = fixture; configure(f)
    monkeypatch.setattr(delivery, "owned_gateway", lambda unused: healthy_gateway(f))
    calls = []
    def run(args, **kwargs):
        calls.append(args); raise subprocess.TimeoutExpired("not-logged", 90)
    result = delivery.launch(f.installation.root, backend=f.backend, run=run)
    assert result["status"] == "launch_outcome_unknown" and result["exit_code"] == 3 and len(calls) == 1

def test_app_status_no_owned_launch_does_not_start_or_adopt_any_process(fixture):
    f = fixture
    result = delivery.status(f.installation.root, backend=f.backend)
    assert result["status"] == "not_launched" and not f.backend.spawned
    with pytest.raises(delivery.DeliveryError):
        delivery.status(f.installation.root, run_id="../default-app", backend=f.backend)

@pytest.mark.parametrize("command,method", [
    ("app-bind", "bind"), ("app-prepare-runtime", "prepare_runtime"),
    ("app-check", "check"), ("app-status", "status"), ("app-launch", "launch"),
])
def test_installed_cli_dispatches_only_the_requested_operation(monkeypatch, capsys, command, method):
    calls = []
    def selected(*args, **kwargs):
        calls.append((args, kwargs)); return {"status": "fixture", "exit_code": 3 if method == "launch" else 0}
    monkeypatch.setattr(delivery, method, selected)
    argv = [command, "--install-root", "chosen-install"]
    if method in {"bind", "prepare_runtime"}:
        argv += ["--native-manifest", "selected-manifest.json", "--native-manifest-sha256", "a"*64]
    code = cli.main(argv)
    assert code == (3 if method == "launch" else 0) and len(calls) == 1
    assert calls[0][0][0] == Path("chosen-install")
    assert json.loads(capsys.readouterr().out)["status"] == "fixture"


@pytest.mark.skipif(os.name != "nt", reason="actual Windows extended path spelling")
def test_extended_path_spelling_preserves_same_installed_identity(tmp_path):
    directory = tmp_path / "release"
    executable = directory / "venv/Scripts/python.exe"
    module = directory / "venv/Lib/site-packages/codex_app_deepseek/app_delivery.py"
    executable.parent.mkdir(parents=True); module.parent.mkdir(parents=True)
    executable.write_bytes(b"not executed"); module.write_text("# not executed")
    extended_exe = "\\\\?\\" + str(executable)
    extended_module = "\\\\?\\" + str(module)
    assert Path(extended_exe).samefile(executable)
    assert Path(extended_module).samefile(module)
    delivery.verify_installed_context(directory, extended_exe, extended_module)
    foreign = tmp_path / "other/python.exe"; foreign.parent.mkdir(); foreign.write_bytes(b"not executed")
    with pytest.raises(delivery.DeliveryError, match="active installed"):
        delivery.verify_installed_context(directory, foreign, extended_module)
    with pytest.raises(delivery.DeliveryError, match="active installed"):
        delivery.verify_installed_context(directory, extended_exe, foreign)


from contextlib import contextmanager
def test_configure_rereads_binding_after_operation_lock(fixture,monkeypatch):
    f=fixture;bind(f)
    replacement=f.manifest.with_name('new-explicit-binding.json')
    value=json.loads(f.manifest.read_text());value['source_sha256']='f'*64;replacement.write_text(json.dumps(value))
    expected=delivery.read_binding(f.installation,f.backend)
    expected['native_manifest']=str(replacement.resolve());expected['native_manifest_sha256']=app.sha(replacement)
    @contextmanager
    def other_bind_finished_before_acquiring_lease(root):
        app.save(delivery.binding_path(f.installation),expected)
        yield
    monkeypatch.setattr(delivery.lc,'_operation_lease',other_bind_finished_before_acquiring_lease)
    delivery.configure(f.installation.root,f.catalog,app.sha(f.catalog),backend=f.backend)
    actual=delivery.read_binding(f.installation,f.backend)
    assert actual['native_manifest']==expected['native_manifest'],'configure silently restored stale binding'
    assert actual['native_manifest_sha256']==expected['native_manifest_sha256']


def test_isolated_auth_module_selection_ignores_python_environment(tmp_path):
    # Exercise the same interpreter flags with --version, never a real token read.
    from codex_app_deepseek import __version__
    shadow=tmp_path/'shadow';package=shadow/'codex_app_deepseek';package.mkdir(parents=True)
    (package/'__init__.py').write_text('')
    (package/'cli.py').write_text('raise RuntimeError("untrusted package selected")\n')
    (shadow/'sitecustomize.py').write_text('raise RuntimeError("untrusted sitecustomize selected")\n')
    env=dict(os.environ);env.update(PYTHONPATH=str(shadow),PYTHONHOME=str(tmp_path/'invalid-python-home'))
    command=[sys.executable,*delivery.AUTH_ARGS[:-1],'--version']
    result=subprocess.run(command,cwd=shadow,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0 and result.stdout.strip()==__version__
    assert 'untrusted' not in result.stderr


def test_installed_preflight_config_is_pinned_into_later_launch_admission(fixture,monkeypatch):
    import tomlkit
    f=fixture;configure(f)
    monkeypatch.setattr(delivery,'owned_gateway',lambda unused:healthy_gateway(f))
    checked=delivery.check(f.installation.root,backend=f.backend)
    path=f.paths['home']/'config.toml';config=tomlkit.parse(path.read_text())
    config['model_providers']['codex_app_deepseek']['base_url']='http://127.0.0.1:49999/v1'
    path.write_text(tomlkit.dumps(config))
    with pytest.raises(app.LaunchError,match='configuration changed'):
        app.prepare_launch(checked['request'],f.backend)
    assert not f.backend.spawned


def test_installed_launch_refuses_missing_preflight_checksum(fixture,monkeypatch):
    f=fixture
    monkeypatch.setattr(delivery,'check',lambda *a,**k:{'request':f.request,'launch':{'config_sha256':'a'*64}})
    with pytest.raises(delivery.DeliveryError,match='requires the verified configuration checksum'):
        delivery.launch(f.installation.root,backend=f.backend,run=lambda *a,**k:pytest.fail('unbound launch executed'))


@pytest.mark.skipif(os.name!='nt',reason='actual Windows PowerShell transport')
@pytest.mark.parametrize('checksum',[None,'a'*64,'invalid'])
def test_powershell_check_preserves_expected_config_with_no_real_app_or_credentials(tmp_path,checksum):
    capture=tmp_path/'captured-request.json';fake=tmp_path/'fake_interpreter.py'
    fake.write_text('import sys,base64,json\nfrom pathlib import Path\nr=json.loads(base64.b64decode(sys.argv[sys.argv.index("--request-base64")+1]))\nPath('+repr(str(capture))+').write_text(json.dumps(r))\nprint(json.dumps({"fixture":True}))\n')
    shim=tmp_path/'fake-python.cmd';shim.write_bytes(('@echo off\r\n"'+sys.executable+'" -I "'+str(fake)+'" %*\r\n').encode())
    baseline=tmp_path/'baseline.json';baseline.write_text('{}');manifest=tmp_path/'manifest.json';manifest.write_text('{}')
    args=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(delivery.assets()[1]),'-Mode','Check','-PythonPath',str(shim),'-BaselinePath',str(baseline),'-NativeManifest',str(manifest),'-NativeManifestSha256','b'*64]
    if checksum is not None:args+=['-ExpectedConfigSha256',checksum]
    result=subprocess.run(args,capture_output=True,text=True,errors='replace',timeout=30)
    if checksum=='invalid':
        assert result.returncode!=0 and not capture.exists()
    else:
        assert result.returncode==0,result.stderr
        request=json.loads(capture.read_text())
        if checksum is None:assert 'expected_config_sha256' not in request
        else:assert request['expected_config_sha256']==checksum
        assert request['native_manifest_sha256']=='b'*64


@pytest.mark.parametrize("missing", ["guardian", "serial", "schema"])
def test_configure_refuses_incomplete_existing_policy_before_app_writes(fixture, missing):
    f = fixture; bind(f)
    policy = gp.default_app_policy()
    if missing == "guardian": policy["accepted_models"].remove("gpt-5.6-luna")
    else: policy["allowed_providers"].remove("primeintellect" if missing == "serial" else "morph/fp8")
    path = f.installation.data / "gateway.json"; path.write_text(json.dumps(policy))
    raw = path.read_bytes(); app_before = (f.paths["home"] / "config.toml").read_bytes()
    with pytest.raises(gp.ServiceConfigError):
        delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    assert path.read_bytes() == raw and (f.paths["home"] / "config.toml").read_bytes() == app_before
    assert not (f.installation.data / "app-catalogs").exists()
    assert delivery.read_binding(f.installation, f.backend)["catalog"] is None
    assert not f.backend.spawned


def test_explicit_extra_alias_catalog_policy_and_reordered_health_are_supported(fixture, monkeypatch):
    f = fixture; bind(f)
    names = [*gp.REQUEST_MODEL_IDS, "explicit-memory-helper"]
    f.catalog.write_text(json.dumps({"models": [{"slug": x} for x in reversed(names)]}))
    path = f.installation.data / "gateway.json"
    path.write_text(json.dumps({"accepted_models": names, "allowed_providers": list(reversed(gp.DEFAULT_PROVIDERS))}))
    before = path.read_bytes()
    delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    gateway = healthy_gateway(f)
    gateway["health"]["accepted_models"].reverse(); gateway["health"]["allowed_providers"].reverse()
    monkeypatch.setattr(delivery, "owned_gateway", lambda _: gateway)
    assert delivery.check(f.installation.root, backend=f.backend)["status"] == "validated_not_launched"
    assert path.read_bytes() == before and not f.backend.spawned


@pytest.mark.parametrize("mismatch", ["hash", "model", "guardian", "providers", "missing_hash", "ordinary", "missing_ordinary"])
def test_app_check_refuses_stale_actual_gateway_policy_without_restart(fixture, monkeypatch, mismatch):
    f = fixture; configure(f)
    gateway = healthy_gateway(f)
    if mismatch == "hash": gateway["health"]["config_sha256"] = "f" * 64
    elif mismatch == "missing_hash": gateway["health"].pop("config_sha256")
    elif mismatch == "ordinary": gateway["health"]["ordinary_default_provider"] = "morph/fp8"
    elif mismatch == "missing_ordinary": gateway["health"].pop("ordinary_default_provider")
    elif mismatch == "model": gateway["health"]["model"] = "unknown-model"
    elif mismatch == "guardian": gateway["health"]["accepted_models"] = [gp.PINNED_MODEL]
    else: gateway["health"]["allowed_providers"].remove("primeintellect")
    monkeypatch.setattr(delivery, "owned_gateway", lambda _: gateway)
    before = (f.paths["home"] / "config.toml").read_bytes()
    with pytest.raises(delivery.DeliveryError, match="different policy/catalog"):
        delivery.check(f.installation.root, backend=f.backend)
    assert (f.paths["home"] / "config.toml").read_bytes() == before and not f.backend.spawned


def test_app_check_rejects_policy_edit_after_process_start(fixture, monkeypatch):
    f = fixture; configure(f)
    gateway = healthy_gateway(f)
    monkeypatch.setattr(delivery, "owned_gateway", lambda _: gateway)
    path = f.installation.data / "gateway.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(delivery.DeliveryError, match="different policy/catalog"):
        delivery.check(f.installation.root, backend=f.backend)
    assert not f.backend.spawned


def test_catalog_alias_mismatch_is_not_silently_renamed(fixture):
    f = fixture; bind(f)
    policy = gp.default_app_policy(); policy["accepted_models"].append("unlisted-helper")
    path = f.installation.data / "gateway.json"; path.write_text(json.dumps(policy))
    before = path.read_bytes()
    with pytest.raises(gp.ServiceConfigError, match="catalog model names differ"):
        delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    assert path.read_bytes() == before and not (f.installation.data / "app-catalogs").exists()
    assert not f.backend.spawned


def test_configure_refuses_foreign_home_before_policy_catalog_or_config_writes(fixture):
    f = fixture
    bind(f)
    f.backend.home_owner_mismatch = True
    before = (f.paths["home"] / "config.toml").read_bytes()
    with pytest.raises(app.LaunchError, match="home owner differs"):
        delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    assert not (f.installation.data / "app-catalogs").exists()
    assert not (f.installation.data / "gateway.json").exists()
    assert not (f.installation.data / "config-backups").exists()
    assert (f.paths["home"] / "config.toml").read_bytes() == before
    assert not f.backend.spawned

@pytest.mark.parametrize("operation",["bind","configure"])
@pytest.mark.parametrize("foreign",["default","separate"])
def test_installed_binding_and_configuration_allow_verified_foreign_profiles_under_locks(fixture,monkeypatch,operation,foreign):
    from test_isolated_app import _foreign_app
    f=fixture
    if operation=="configure":bind(f)
    other=str(f.paths["home"].parent/"separate-foreign-home")
    state=_foreign_app(f.backend,**({} if foreign=="default" else {
        "home":other,"frontend":str(Path(other)/"electron"),"browser":str(Path(other)/"browser")}))
    initial=copy.deepcopy(state);locks=set();observed=[]
    @contextmanager
    def operation_lock(root):
        assert root==f.installation.root
        locks.add("installation")
        try:yield
        finally:locks.remove("installation")
    @contextmanager
    def launch_lock(sid):
        assert sid==f.backend.current()["sid"]
        locks.add("launch")
        try:yield
        finally:locks.remove("launch")
    read=f.backend.selected_environment
    def check(row):
        assert locks=={"installation","launch"}
        observed.append(row["pid"])
        return read(row)
    monkeypatch.setattr(delivery.lc,"_operation_lease",operation_lock)
    f.backend.launch_lock=launch_lock;f.backend.selected_environment=check
    result=(bind(f) if operation=="bind" else delivery.configure(
        f.installation.root,f.catalog,app.sha(f.catalog),backend=f.backend))
    assert result["status"]==("runtime_bound_not_launched" if operation=="bind" else "private_app_configured_not_launched")
    assert observed==[999] and state==initial and not f.backend.spawned
    binding=delivery.read_binding(f.installation,f.backend)
    assert binding["native_manifest"]==str(f.manifest.resolve())
    if operation=="configure":
        assert binding["catalog"]["sha256"]==app.sha(f.catalog)
        assert Path(result["checkpoint"]).is_file()


@pytest.mark.parametrize("operation",["bind","configure"])
@pytest.mark.parametrize("fault",["same-home","same-frontend","same-browser","ambiguous","reused","unknown"])
def test_binding_and_configuration_reject_real_or_uncertain_profile_conflicts_before_writes(fixture,operation,fault):
    from test_isolated_app import _foreign_app
    f=fixture
    if operation=="configure":bind(f)
    state=_foreign_app(f.backend)
    if fault=="same-home":state["environment"]["CODEX_HOME"]=str(f.paths["home"]).swapcase()
    elif fault=="same-frontend":state["environment"]["CODEX_ELECTRON_USER_DATA_PATH"]=str(f.paths["frontend"])
    elif fault=="same-browser":state["arguments"]=["--user-data-dir="+str(f.paths["frontend"])]
    elif fault=="ambiguous":state["arguments"]=["--user-data-dir"]
    elif fault=="reused":state["identity"]["created"]+=1
    else:
        def denied(row):raise app.psutil.AccessDenied(row["pid"])
        f.backend.selected_environment=denied
    original=(f.paths["home"]/"config.toml").read_bytes()
    before={p.relative_to(f.installation.data).as_posix():p.read_bytes()
            for p in f.installation.data.rglob("*") if p.is_file()}
    with pytest.raises(delivery.DeliveryError):
        if operation=="bind":bind(f)
        else:delivery.configure(f.installation.root,f.catalog,app.sha(f.catalog),backend=f.backend)
    after={p.relative_to(f.installation.data).as_posix():p.read_bytes()
           for p in f.installation.data.rglob("*") if p.is_file()}
    assert after==before and (f.paths["home"]/"config.toml").read_bytes()==original
    assert not f.backend.spawned


def test_configuration_with_foreign_App_keeps_three_way_rollback_and_later_user_edits(fixture):
    from test_isolated_app import _foreign_app
    from codex_app_deepseek.app_config import restore
    import tomlkit
    f=fixture;path=f.paths["home"]/"config.toml"
    original='# Original user setting\nmodel_provider = "codex_app_deepseek"\n\n[user]\nkeep = "original"\n'
    path.write_text(original)
    state=_foreign_app(f.backend);before=copy.deepcopy(state)
    result=configure(f)
    value=tomlkit.parse(path.read_text());value["user"]["later"]="external edit"
    path.write_text(tomlkit.dumps(value))
    restore(Path(result["checkpoint"]))
    restored=tomlkit.parse(path.read_text())
    assert restored["model_provider"]=="codex_app_deepseek"
    assert restored["user"]=={"keep":"original","later":"external edit"}
    assert "model_catalog_json" not in restored and "model_providers" not in restored
    assert path.read_text().count("# Original user setting")==1
    assert state==before and not f.backend.spawned


@pytest.fixture
def prior_cohort(fixture, monkeypatch):
    """Retained installation verification is a lifecycle boundary, never executed here."""
    f = fixture
    configure(f)
    original = delivery.read_binding(f.installation, f.backend)
    old_id = "b" * 20
    prior = f.installation.root / "releases" / old_id
    baseline = prior / "venv/Lib/site-packages/codex_app_deepseek/assets/app-launch-baseline.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(json.dumps({"version": "26.1016.8120.0", "native_version": "0.163.0-alpha.11.1"}))
    old_hash = app.sha(baseline)
    monkeypatch.setattr(delivery, "PRIOR_APP_BASELINES", {old_hash: ("26.1016.8120.0", "0.163.0-alpha.11.1")})
    receipt = prior / "installation.json"
    receipt.write_text(json.dumps({"schema_version": 1, "project": delivery.lc.PROJECT,
        "release_id": old_id, "data_home": str(f.installation.data)}))
    verified = []
    def verify(path, value):
        assert path == prior and value == json.loads(receipt.read_text())
        verified.append(path)
    monkeypatch.setattr(delivery.lc, "_verify_installation", verify)
    old = {**original, "gateway_release_id": old_id, "app_baseline_sha256": old_hash}
    path = delivery.binding_path(f.installation)
    app.save(path, old)
    return SimpleNamespace(f=f, old=old, raw=path.read_bytes(), receipt=receipt,
                           baseline=baseline, verified=verified)


def test_explicit_cohort_upgrade_keeps_recoverable_binding_but_requires_new_catalog(prior_cohort):
    old = prior_cohort; f = old.f
    config = (f.paths["home"] / "config.toml").read_bytes()
    with pytest.raises(delivery.DeliveryError, match="bind again"):
        delivery.read_binding(f.installation, f.backend, previous_release=True)
    with pytest.raises(delivery.DeliveryError, match="bind again"):
        delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    result = bind(f)
    assert result["catalog_reconfiguration_required"] is True
    assert old.verified == [old.receipt.parent]
    current = delivery.read_binding(f.installation, f.backend)
    assert current["app_baseline_sha256"] == app.BASELINE_SHA256 and current["catalog"] is None
    assert current["gateway_release_id"] == f.installation.receipt["release_id"]
    backup = Path(result["prior_cohort_backup"])
    assert (backup / "binding.before.json").read_bytes() == old.raw
    assert (backup / "prior-installation.json").read_bytes() == old.receipt.read_bytes()
    assert (backup / "prior-app-baseline.json").read_bytes() == old.baseline.read_bytes()
    record = json.loads((backup / "migration.json").read_bytes())
    assert record["before_sha256"] == hashlib.sha256(old.raw).hexdigest()
    assert record["planned_binding_sha256"] == app.sha(delivery.binding_path(f.installation))
    assert (f.paths["home"] / "config.toml").read_bytes() == config and not f.backend.spawned
    # Explicit configure is the only operation that installs a new cohort catalog.
    delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    assert delivery.read_binding(f.installation, f.backend)["catalog"] is not None


@pytest.mark.parametrize("fault", ["unknown-baseline", "baseline-container", "foreign-owner", "bad-schema",
    "bad-release", "escaped-catalog", "foreign-data", "missing-receipt", "changed-baseline", "unverified-install"])
def test_cohort_upgrade_rejects_unknown_or_unowned_provenance_before_writes(prior_cohort, monkeypatch, fault):
    old = prior_cohort; f = old.f; path = delivery.binding_path(f.installation)
    value = dict(old.old)
    if fault == "unknown-baseline": value["app_baseline_sha256"] = "f" * 64
    elif fault == "baseline-container": value["app_baseline_sha256"] = []
    elif fault == "foreign-owner": value["owner_sid"] = "S-1-5-21-2-9999"
    elif fault == "bad-schema": value["schema_version"] = True
    elif fault == "bad-release": value["gateway_release_id"] = "../another"
    elif fault == "escaped-catalog": value["catalog"] = {"path": str(f.catalog), "sha256": app.sha(f.catalog)}
    elif fault == "foreign-data":
        receipt = json.loads(old.receipt.read_bytes()); receipt["data_home"] = str(f.installation.data.parent / "foreign")
        old.receipt.write_text(json.dumps(receipt))
    elif fault == "missing-receipt": old.receipt.unlink()
    elif fault == "changed-baseline": old.baseline.write_text("{}")
    else:
        def fail(*args): raise delivery.lc.LifecycleError("Installation contains modified files")
        monkeypatch.setattr(delivery.lc, "_verify_installation", fail)
    app.save(path, value)
    before = {p.relative_to(f.installation.data).as_posix(): p.read_bytes()
              for p in f.installation.data.rglob("*") if p.is_file()}
    config = (f.paths["home"] / "config.toml").read_bytes()
    with pytest.raises((delivery.DeliveryError, delivery.lc.LifecycleError, ValueError, OSError)):
        bind(f)
    after = {p.relative_to(f.installation.data).as_posix(): p.read_bytes()
             for p in f.installation.data.rglob("*") if p.is_file()}
    assert after == before and (f.paths["home"] / "config.toml").read_bytes() == config
    assert not f.backend.spawned


@pytest.mark.parametrize("fault", ["duplicate-key", "hardlink", "directory"])
def test_binding_snapshot_rejects_ambiguous_or_aliased_files(fixture, fault):
    f = fixture; bind(f); path = delivery.binding_path(f.installation)
    if fault == "duplicate-key":
        path.write_bytes(path.read_bytes().replace(b'{', b'{"owner_sid":"foreign",', 1))
    elif fault == "hardlink": os.link(path, path.with_name("binding-alias.json"))
    else: path.unlink(); path.mkdir()
    with pytest.raises(delivery.DeliveryError):
        delivery.read_binding(f.installation, f.backend)
    assert not f.backend.spawned


def test_cohort_binding_cas_preserves_an_external_selection_during_backup(prior_cohort, monkeypatch):
    old = prior_cohort; f = old.f; path = delivery.binding_path(f.installation)
    save = delivery._backup_prior_binding; external = old.raw + b" "
    def backup(*args):
        result = save(*args)
        path.write_bytes(external)
        return result
    monkeypatch.setattr(delivery, "_backup_prior_binding", backup)
    with pytest.raises(delivery.DeliveryError, match="later selection preserved"):
        bind(f)
    assert path.read_bytes() == external and not f.backend.spawned


def test_cohort_backup_refuses_preexisting_inconsistent_bytes(prior_cohort):
    old = prior_cohort; f = old.f
    planned = {**old.old, "gateway_release_id": f.installation.receipt["release_id"],
               "app_baseline_sha256": app.BASELINE_SHA256, "native_manifest": str(f.manifest.resolve()),
               "native_manifest_sha256": app.sha(f.manifest), "catalog": None}
    planned_hash = hashlib.sha256((json.dumps(planned, indent=2) + "\n").encode()).hexdigest()
    before_hash = hashlib.sha256(old.raw).hexdigest()
    transition = hashlib.sha256((before_hash + ":" + planned_hash).encode("ascii")).hexdigest()
    backup = f.installation.data / "app-binding-backups" / transition
    backup.mkdir(parents=True)
    (backup / "binding.before.json").write_bytes(b"unrelated earlier file")
    with pytest.raises(delivery.DeliveryError, match="backup changed"):
        bind(f)
    assert delivery.binding_path(f.installation).read_bytes() == old.raw and not f.backend.spawned


def test_binding_backup_rollback_hash_precondition_preserves_later_selection(prior_cohort):
    from codex_app_deepseek.app_config import ConfigurationConflict
    old = prior_cohort; f = old.f; result = bind(f)
    backup = Path(result["prior_cohort_backup"])
    record = json.loads((backup / "migration.json").read_bytes())
    path = delivery.binding_path(f.installation)
    upgraded = path.read_bytes()
    # The recorded recovery rule can restore exactly the known upgrade bytes.
    delivery._atomic_write(path, old.raw, expected_sha256=record["planned_binding_sha256"])
    assert path.read_bytes() == old.raw
    delivery._atomic_write(path, upgraded)
    external = upgraded + b" "
    path.write_bytes(external)
    with pytest.raises(ConfigurationConflict):
        delivery._atomic_write(path, old.raw, expected_sha256=record["planned_binding_sha256"])
    assert path.read_bytes() == external


@pytest.mark.skipif(os.name != "nt", reason="actual Windows directory junction")
def test_cohort_upgrade_rejects_prior_installation_junction_before_resolving(prior_cohort):
    old = prior_cohort; f = old.f; prior = old.receipt.parent
    target = prior.parent / "retained" / prior.name
    target.parent.mkdir(); prior.rename(target)
    result = subprocess.run([r"C:\Windows\System32\cmd.exe", "/d", "/c", "mklink", "/J", str(prior), str(target)],
                            capture_output=True, text=True, errors="replace")
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(ValueError, match="junction"):
            bind(f)
        assert delivery.binding_path(f.installation).read_bytes() == old.raw
        assert not (f.installation.data / "app-binding-backups").exists()
    finally:
        os.rmdir(prior)  # Remove only the junction; retained fixture bytes remain.


def test_cohort_backup_survives_rollback_then_upgrade_to_another_gateway_release(prior_cohort, monkeypatch):
    old = prior_cohort; f = old.f
    first = bind(f)
    backup = Path(first["prior_cohort_backup"])
    initial = {p.name: p.read_bytes() for p in backup.iterdir()}
    app.save(delivery.binding_path(f.installation), old.old)
    f.installation = delivery.Installation(f.installation.root, f.installation.directory,
                                          f.installation.data, {"release_id": "c" * 20})
    monkeypatch.setattr(delivery, "installed", lambda root: f.installation)
    second = bind(f)
    second_backup = Path(second["prior_cohort_backup"])
    assert second_backup != backup and second_backup.parent == backup.parent
    assert {p.name: p.read_bytes() for p in backup.iterdir()} == initial
    assert (second_backup / "binding.before.json").read_bytes() == old.raw
    assert delivery.read_binding(f.installation, f.backend)["gateway_release_id"] == "c" * 20


def test_cohort_retained_receipt_change_during_verification_cannot_be_backed_up(prior_cohort, monkeypatch):
    old = prior_cohort; f = old.f
    def concurrent_receipt_edit(*args):
        old.receipt.write_bytes(old.receipt.read_bytes() + b" ")
    monkeypatch.setattr(delivery.lc, "_verify_installation", concurrent_receipt_edit)
    with pytest.raises(delivery.DeliveryError, match="provenance changed"):
        bind(f)
    assert delivery.binding_path(f.installation).read_bytes() == old.raw
    assert not (f.installation.data / "app-binding-backups").exists()


def test_fresh_defaults_precede_native_config_and_noop_does_not_duplicate_checkpoints(fixture, monkeypatch):
    import tomllib, tomlkit
    from codex_app_deepseek import native_app_tools as tools
    f = fixture; bind(f)
    config = f.paths["home"] / "config.toml"
    config.unlink()
    registered = []
    monkeypatch.setattr(delivery.lc, "register_checkpoint", lambda root, path: registered.append(path))
    def bootstrap(*args, **kwargs):
        assert len(registered) == 1
        document = tomlkit.parse(config.read_text())
        assert document["sandbox_mode"] == "workspace-write"
        assert document["approval_policy"] == "on-request"
        document["plugins"] = {tools.PLUGIN_ID: {"enabled": True, "mcp_servers": {tools.SERVER: {"enabled": True}}}}
        config.write_bytes(tomlkit.dumps(document).encode())
        return {"state": "configured"}
    monkeypatch.setattr(tools, "bootstrap", bootstrap)
    result = delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    assert len(result["checkpoints"]) == len(registered) == 1
    manifest = json.loads(Path(result["checkpoint"]).read_bytes())
    assert not manifest["was_present"] and tools.POLICY_KEY not in manifest["changed_settings"]
    assert tomllib.loads(config.read_text())["plugins"][tools.PLUGIN_ID]["mcp_servers"][tools.SERVER]["enabled"]
    monkeypatch.setattr(tools, "bootstrap", lambda *args, **kwargs: {"state": "configured"})
    again = delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    assert again["checkpoint"] is None and again["checkpoints"] == [] and len(registered) == 1


def test_provider_is_reprepared_from_native_cli_bytes_before_second_checkpoint(fixture, monkeypatch):
    import tomlkit
    from codex_app_deepseek import native_app_tools as tools
    f = fixture; bind(f)
    config = f.paths["home"] / "config.toml"
    registered, captured = [], []
    monkeypatch.setattr(delivery.lc, "register_checkpoint", lambda root, path: registered.append(path))
    def bootstrap(*args, **kwargs):
        assert len(registered) == 1
        document = tomlkit.parse(config.read_text())
        document["web_search"] = "disabled"
        document["plugins"] = {tools.PLUGIN_ID: {"enabled": True}}
        config.write_bytes(tomlkit.dumps(document).encode())
        captured.append(app.sha(config))
        return {"state": "configured"}
    monkeypatch.setattr(tools, "bootstrap", bootstrap)
    result = delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    assert len(result["checkpoints"]) == len(registered) == 2
    second = json.loads(Path(result["checkpoints"][-1]).read_bytes())
    assert second["before_sha256"] == captured[0] and second["changed_settings"] == ["web_search"]
    assert tomlkit.parse(config.read_text())["plugins"][tools.PLUGIN_ID]["enabled"]


def test_legacy_mcp_conflict_precedes_catalog_policy_and_provider_publication(fixture, monkeypatch):
    from codex_app_deepseek import native_app_tools as tools
    f = fixture; bind(f)
    config = f.paths["home"] / "config.toml"
    original = config.read_bytes() + b'\n[mcp_servers.codex_app]\ncommand="user-owned"\n'
    config.write_bytes(original)
    monkeypatch.setattr(delivery.gp, "ensure_app_policy", lambda *args: pytest.fail("No policy write before conflict check"))
    monkeypatch.setattr(delivery.lc, "register_checkpoint", lambda *args: pytest.fail("No checkpoint before conflict check"))
    with pytest.raises(tools.NativeToolsConflict, match="Legacy"):
        delivery.configure(f.installation.root, f.catalog, app.sha(f.catalog), backend=f.backend)
    assert config.read_bytes() == original and not (f.installation.data / "app-catalogs").exists()


def test_explicit_trace_root_survives_installed_preflight_and_powershell_argv(fixture, tmp_path, monkeypatch):
    f = fixture; configure(f)
    monkeypatch.setattr(delivery, "owned_gateway", lambda unused: healthy_gateway(f))
    root = tmp_path / "trace container"
    checked = delivery.check(f.installation.root, backend=f.backend, trace_root=root)
    assert checked["request"]["trace_root"] == str(root) and not root.exists()
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=2, stdout=json.dumps({"status": "observation_timeout"}))
    delivery.launch(f.installation.root, backend=f.backend, trace_root=root, run=run)
    assert len(calls) == 1 and calls[0][0][calls[0][0].index("-TraceRoot") + 1] == str(root)
    assert "env" not in calls[0][1] and not root.exists()


def test_cli_explicit_trace_root_is_forwarded_only_to_requested_launch(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(delivery, "launch", lambda *a, **k: calls.append((a, k)) or {"status": "fixture"})
    root = tmp_path / "trace"
    assert cli.main(["app-launch", "--install-root", "owned", "--trace-root", str(root)]) == 0
    assert len(calls) == 1 and calls[0][1]["trace_root"] == root
    assert not root.exists()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows PowerShell request transport")
@pytest.mark.parametrize("trace", [None, "E:\\Owned private evidence\\trace"])
def test_powershell_passes_only_explicit_trace_root_without_app_activation(tmp_path, trace):
    capture = tmp_path / "request.json"; fake = tmp_path / "fake.py"
    fake.write_text('import sys,base64,json\nfrom pathlib import Path\nr=json.loads(base64.b64decode(sys.argv[sys.argv.index("--request-base64")+1]))\nPath('+repr(str(capture))+').write_text(json.dumps(r))\nprint("{}")\n')
    shim = tmp_path / "fake-python.cmd"
    shim.write_bytes(('@echo off\r\n"'+sys.executable+'" -I "'+str(fake)+'" %*\r\n').encode())
    baseline = tmp_path / "baseline.json"; baseline.write_text('{}')
    manifest = tmp_path / "manifest.json"; manifest.write_text('{}')
    args = ['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(delivery.assets()[1]),'-Mode','Check','-PythonPath',str(shim),'-BaselinePath',str(baseline),'-NativeManifest',str(manifest),'-NativeManifestSha256','b'*64]
    if trace is not None: args += ['-TraceRoot', trace]
    run = subprocess.run(args, capture_output=True, text=True, errors='replace', timeout=30)
    assert run.returncode == 0, run.stderr
    request = json.loads(capture.read_text())
    assert (request.get('trace_root') == trace) if trace is not None else ('trace_root' not in request)
