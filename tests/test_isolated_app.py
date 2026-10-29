import copy
from contextlib import nullcontext
import json
from pathlib import Path
import pytest
from codex_app_deepseek import isolated_app as app

@pytest.fixture
def fixture(tmp_path, monkeypatch):
    paths = {"home": tmp_path / "home", "frontend": tmp_path / "home/frontend", "launches": tmp_path / "launches", "native_logs": tmp_path / "native-logs"}
    paths["home"].mkdir(); (paths["home"] / "config.toml").write_text('model_provider = "codex_app_deepseek"\n')
    monkeypatch.setattr(app, "defaults", lambda environment=None: paths)
    directory = tmp_path / "native"; directory.mkdir()
    names = ["codex.exe", "codex-command-runner.exe", "codex-windows-sandbox-setup.exe", "codex-windows-managed-deny-probe.exe"]
    host = directory / "codex-code-mode-host.exe"; host.write_bytes(b"fixture host never executed")
    expected = {"schema_version": 1, "package_name": "OpenAI.Codex", "package_full_name": "fixed-package",
                "package_family_name": "fixed-family", "version": "fixed-version", "publisher": "fixed-publisher",
                "required_signature_kind": "Store", "executable_signer_thumbprint": "THUMB", "executable": "app/ChatGPT.exe",
                "files": {"app/ChatGPT.exe": "fixed"}, "app_resources": {"app/resources/host.exe": {"sha256": app.sha(host), "size": host.stat().st_size}},
                "native_version": "fixed-native", "native_upstream_commit": "fixed-upstream", "native_binaries": names,
                "required_private_sidecars": {"codex-code-mode-host.exe": "app/resources/host.exe"}}
    baseline = tmp_path / "baseline.json"; baseline.write_text(json.dumps(expected))
    monkeypatch.setattr(app, "BASELINE_SHA256", app.sha(baseline))
    rows = []
    for name in [*names, host.name]:
        file = directory / name
        if not file.exists(): file.write_bytes(("fixture " + name + " never executed").encode())
        rows.append({"name": name, "path": str(file), "size": file.stat().st_size, "sha256": app.sha(file)})
    manifest = {"schema_version": 1, "status": "prepared-not-app-qualified", "native_version": "fixed-native",
                "upstream_commit": "fixed-upstream", "source_sha256": "1" * 64, "app_baseline_sha256": app.BASELINE_SHA256,
                "app_package_full_name": "fixed-package", "outputs": rows}
    manifest_path = directory / "app-runtime-manifest.json"; manifest_path.write_text(json.dumps(manifest))
    request = {"baseline": str(baseline), "native_manifest": str(manifest_path), "native_manifest_sha256": app.sha(manifest_path)}
    class Backend:
        def __init__(self):
            self.current_value = {"sid": "S-1-5-21-1-1001", "session": 1, "elevated": False, "package_full_name": "fixed-package"}
            self.existing = []; self.spawned = []; self.native_home_override = None
        def current(self): return dict(self.current_value)
        def trusted_git(self):
            return {"status": "unavailable", "path": None, "version": None,
                    "diagnostic": "fixture has no trusted Git", "worktree_gate": "pending_app_validation"}
        def git_path_contains(self, identity, directory):
            return app.path_contains_directory(self.spawned[-1][2], directory)
        def live(self, identity): return identity["pid"] != 102
        def home_guard(self, path, sid, *, create=False):
            if getattr(self, "home_owner_mismatch", False):
                raise app.LaunchError("dedicated App home owner differs from the intended Windows user")
            if create:
                Path(path).mkdir(exist_ok=True)
            return nullcontext({"owner_sid": sid, "created": False,
                                "scope": "fixture_home", "permissions_changed": False})
        def package(self, unused):
            return {"package": {"full_name": "fixed-package", "family": "fixed-family", "version": "fixed-version", "publisher": "fixed-publisher", "location": str(tmp_path / "package"), "status": "Ok", "signature_kind": "Store", "development": False},
                    "exe_signature": {"status": "Valid", "thumbprint": "THUMB"}, "package_signature_status": "Valid",
                    "files": {**expected["files"], **{k: v["sha256"] for k, v in expected["app_resources"].items()}}}
        def existing_apps(self, unused): return self.existing
        def launch_lock(self, unused): return nullcontext()
        def spawn(self, executable, arguments, environment, cwd):
            self.spawned.append((executable, arguments, environment, cwd))
            return type("Process", (), {"pid": 101})()
        def identity(self, pid):
            return {"pid": pid, "created": 1.0, "sid": self.current_value["sid"], "session": self.current_value["session"], "exe": str(tmp_path / "package/app/ChatGPT.exe")}
        def selected_environment(self, identity):
            values = {key: self.spawned[-1][2][key] for key in app.CONTROLLED}
            if identity["pid"] == 102 and self.native_home_override is not None: values["CODEX_HOME"] = self.native_home_override
            return values
        def profile_arguments(self, identity): return [arg for arg in self.spawned[-1][1] if arg.startswith("--user-data-dir=")]
        def native_role(self, identity): return "app-server"
        def descendants(self, root): return [root, {**root, "pid": 102, "exe": str(directory / "codex.exe")}]
        def native_control_plane(self, root, *, executable, expected_sha256, environment):
            assert Path(executable) == directory / "codex.exe"
            assert expected_sha256 == manifest["outputs"][0]["sha256"]
            assert environment["CODEX_CLI_PATH"] == str(executable)
            return self.descendants(root)
    return paths, expected, manifest_path, manifest, request, Backend()

def test_child_environment_filters_only_explicit_selectors_and_preserves_parent():
    original = {"PATH": "keep", "LOCALAPPDATA": "keep", "HTTP_PROXY": "keep", "MY_WORK_SETTING": "keep",
                "openai_api_key": "never-log", "CODEX_DEEPSEEK_HOME": "never-log", "CODEX_SQLITE_HOME": "never-log",
                "CODEX_HOME": "old", "CODEX_WINDOWS_REGISTERED_CORE": "1", "OPENAI_PROJECT": "never-log"}
    before = dict(original)
    values = {"CODEX_HOME": "private", "CODEX_CLI_PATH": "private-exe", "CODEX_ELECTRON_USER_DATA_PATH": "frontend", "CODEX_APP_SERVER_FORCE_CLI": "1"}
    result, removed = app.child_environment(original, values)
    assert original == before
    assert all(result[key] == "keep" for key in ["PATH", "LOCALAPPDATA", "HTTP_PROXY", "MY_WORK_SETTING"])
    assert not any(key.upper() in app.REMOVED or key.upper().startswith("CODEX_DEEPSEEK_") for key in result)
    assert "never-log" not in json.dumps(removed)
    assert {key: result[key] for key in app.CONTROLLED} == values

def test_preflight_has_no_launch_or_filesystem_admission(fixture):
    paths, expected, manifest_path, manifest, request, backend = fixture
    result = app.validate_request(request, backend)
    assert result["environment"]["CODEX_ELECTRON_USER_DATA_PATH"] == str(paths["frontend"])
    assert not paths["launches"].exists() and not paths["frontend"].exists() and not backend.spawned

@pytest.mark.parametrize("mutation", ["manifest-hash", "missing-host", "binary-corrupt", "native-version", "wrong-package", "directory-escape"])
def test_bundle_fails_closed(fixture, mutation):
    paths, expected, path, value, request, backend = fixture
    if mutation == "manifest-hash": request["native_manifest_sha256"] = "0" * 64
    elif mutation == "missing-host": value["outputs"] = value["outputs"][:-1]
    elif mutation == "binary-corrupt": Path(value["outputs"][0]["path"]).write_bytes(b"changed")
    elif mutation == "native-version": value["native_version"] = "other"
    elif mutation == "wrong-package": value["app_package_full_name"] = "other"
    else: value["outputs"][0]["path"] = str(path.parent.parent / "codex.exe")
    if mutation not in {"manifest-hash", "binary-corrupt"}:
        path.write_text(json.dumps(value)); request["native_manifest_sha256"] = app.sha(path)
    with pytest.raises(app.LaunchError): app.validate_request(request, backend)
    assert not backend.spawned

@pytest.mark.parametrize("mutation", ["session0", "elevated", "existing-app", "unknown-field", "userdata-argument"])
def test_launch_admission_rejections(fixture, mutation):
    paths, expected, path, value, request, backend = fixture
    if mutation == "session0": backend.current_value["session"] = 0
    elif mutation == "elevated": backend.current_value["elevated"] = True
    elif mutation == "existing-app": backend.existing = [{"pid": 999}]
    elif mutation == "unknown-field": request["fallback"] = True
    else: request["app_args"] = ["--user-data-dir=old-profile"]
    with pytest.raises(app.LaunchError): app.prepare_launch(request, backend)
    assert not backend.spawned and not paths["launches"].exists()

def test_mocked_launch_records_exact_backend_and_is_not_acceptance(fixture):
    paths, expected, path, value, request, backend = fixture
    plan = app.prepare_launch(request, backend)
    result = app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    assert len(backend.spawned) == 1
    assert result["status"] == "started_expected_backend" and result["app_runtime_acceptance"] is False
    assert result["native_processes"][0]["controlled_environment"]["CODEX_HOME"] == str(paths["home"])
    with pytest.raises(FileExistsError): app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    assert len(backend.spawned) == 1

@pytest.mark.parametrize("failure", ["wrong-user", "wrong-session", "wrong-package", "new-existing-app", "native-home"])
def test_inside_failure_does_not_fallback_or_relaunch(fixture, failure):
    paths, expected, path, value, request, backend = fixture
    plan = app.prepare_launch(request, backend)
    if failure == "wrong-user": backend.current_value["sid"] = "S-1-5-21-1-1002"
    elif failure == "wrong-session": backend.current_value["session"] = 2
    elif failure == "wrong-package": backend.current_value["package_full_name"] = "other"
    elif failure == "new-existing-app": backend.existing = [{"pid": 999}]
    else: backend.native_home_override = "foreign-home"
    with pytest.raises(app.LaunchError): app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    assert len(backend.spawned) == (1 if failure == "native-home" else 0)
    receipt = app.load_json(Path(plan["run_dir"]) / "receipt.json")
    assert receipt["status"] == "failed_or_unverified" and receipt["replay_allowed"] is False


def test_status_distinguishes_recorded_success_from_current_liveness(fixture):
    paths, expected, path, value, request, backend = fixture
    plan = app.prepare_launch(request, backend)
    app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    result = app.launch_status(plan["run_dir"], backend)
    assert result["status"] == "observation" and result["recorded_status"] == "started_expected_backend"
    assert [p["live"] for p in result["processes"]] == [True, False]
    assert not result["app_runtime_acceptance"] and len(backend.spawned) == 1
    backend.current_value["sid"] = "S-1-5-21-1-1002"
    with pytest.raises(app.LaunchError): app.launch_status(plan["run_dir"], backend)

@pytest.mark.parametrize("outside", [True, False])
def test_sqlite_home_stays_with_native_home(fixture, outside):
    paths, expected, path, value, request, backend = fixture
    configured = "../foreign-home" if outside else "state-db"
    (paths["home"] / "config.toml").write_text('model_provider = "codex_app_deepseek"\nsqlite_home = ' + json.dumps(configured) + "\n")
    if outside:
        with pytest.raises(app.LaunchError): app.validate_request(request, backend)
    else: app.validate_request(request, backend)
    assert not backend.spawned

@pytest.mark.parametrize("existing_app", [True, False, "foreign-default", "foreign-distinct"])
def test_prepare_runtime_copies_only_official_sidecar_and_never_runs(fixture, existing_app):
    paths, expected, path, value, request, backend = fixture
    original_host = path.parent / "codex-code-mode-host.exe"
    source = Path(backend.package(expected)["package"]["location"]) / "app/resources/host.exe"
    source.parent.mkdir(parents=True); source.write_bytes(original_host.read_bytes())
    original_host.unlink(); path.unlink()
    raw = {k: v for k, v in value.items() if k not in {"app_baseline_sha256", "app_package_full_name"}}
    raw["status"] = "built-not-app-qualified"; raw["outputs"] = raw["outputs"][:-1]
    raw_path = path.parent / "manifest.json"; raw_path.write_text(json.dumps(raw))
    if existing_app is True:
        backend.existing = [{"pid": 999}]
        with pytest.raises(app.LaunchError): app.prepare_runtime(raw_path, app.sha(raw_path), request["baseline"], backend)
        assert not original_host.exists() and not path.exists()
    else:
        state = None
        if existing_app == "foreign-default":state = _foreign_app(backend)
        elif existing_app == "foreign-distinct":
            other = str(paths["home"].parent / "foreign")
            state = _foreign_app(backend,home=other,frontend=other,browser=other)
        before = copy.deepcopy(state)
        source_before = source.read_bytes()
        prepared = app.prepare_runtime(raw_path, app.sha(raw_path), request["baseline"], backend)
        app.verify_bundle(Path(prepared["manifest"]), prepared["manifest_sha256"], expected)
        assert source.read_bytes() == source_before == original_host.read_bytes()
        assert app.prepare_runtime(raw_path, app.sha(raw_path), request["baseline"], backend) == prepared
        assert state == before
    assert not backend.spawned and not paths["launches"].exists()


@pytest.mark.parametrize("which", ["home", "frontend", "launches"])
def test_state_junction_cannot_redirect_to_another_profile(fixture, which, tmp_path):
    import os
    import subprocess
    paths, expected, path, value, request, backend = fixture
    target = tmp_path / ("foreign-" + which)
    target.mkdir()
    marker = target / "preserve.txt"; marker.write_text("preserve")
    alias = paths[which]
    if which == "home":
        (alias / "config.toml").unlink(); alias.rmdir()
        (target / "config.toml").write_text('model_provider = "codex_app_deepseek"\n')
    alias.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        command = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/cmd.exe")
        result = subprocess.run([command, "/d", "/c", "mklink", "/J", str(alias), str(target)], capture_output=True, timeout=10)
        assert result.returncode == 0
    else:
        alias.symlink_to(target, target_is_directory=True)
    before = sorted((p.name, p.read_bytes()) for p in target.iterdir())
    try:
        with pytest.raises(app.LaunchError, match="state path"):
            app.prepare_launch(request, backend)
        assert not backend.spawned
        assert sorted((p.name, p.read_bytes()) for p in target.iterdir()) == before
    finally:
        alias.rmdir() if os.name == "nt" else alias.unlink()


def test_same_path_binary_replacement_after_preflight_is_not_accepted(fixture):
    paths, expected, path, value, request, backend = fixture
    plan = app.prepare_launch(request, backend)
    original = backend.spawn
    def replace_after_admission(*args):
        process = original(*args)
        Path(value["outputs"][0]["path"]).write_bytes(b"changed while starting")
        return process
    backend.spawn = replace_after_admission
    with pytest.raises(app.LaunchError, match="bytes changed"):
        app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    assert len(backend.spawned) == 1
    receipt = app.load_json(Path(plan["run_dir"]) / "receipt.json")
    assert receipt["status"] == "failed_or_unverified" and not receipt["replay_allowed"]

@pytest.mark.parametrize("explicit_baseline", [False, True])
def test_real_windows_powershell_binds_default_baseline_after_parameters(explicit_baseline):
    import os, subprocess, sys
    if os.name != "nt": pytest.skip("Windows PowerShell entrypoint")
    root = Path(__file__).resolve().parents[1]
    args = [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
            str(root / "scripts/launch_isolated_app.ps1"), "-Mode", "Status", "-PythonPath", sys.executable]
    if explicit_baseline:
        args += ["-BaselinePath", str(root / "native/app-launch-baseline.json")]
    result = subprocess.run(args, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert b"Status requires RunDirectory" in result.stderr
    assert b"ParameterArgumentValidationErrorEmptyStringNotAllowed" not in result.stderr

@pytest.mark.parametrize("args, expected", [
    (["codex.exe", "--version"], False),
    (["codex.exe", "-c", "app-server", "--version"], False),
    (["codex.exe", "exec", "app-server"], False),
    (["codex.exe", "-c"], False),
    (["codex.exe", "app-server"], True),
    (["codex.exe", "-c", "features.code_mode_host=true", "app-server", "--analytics-default-enabled"], True),
    (["codex.exe", "--config=features.code_mode_host=true", "app-server"], True),
])
def test_probe_commands_do_not_satisfy_native_backend(args, expected):
    assert app.is_app_server_command(args) is expected


def test_native_probe_only_times_out_without_false_backend_success(fixture, monkeypatch):
    paths, expected, path, value, request, backend = fixture
    plan = app.prepare_launch(request, backend)
    backend.native_role = lambda identity: "other-native-command"
    ticks = iter([0, 1, 61])
    monkeypatch.setattr(app.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(app.time, "sleep", lambda unused: None)
    with pytest.raises(app.LaunchError, match="expected backend not observed"):
        app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    receipt = app.load_json(Path(plan["run_dir"]) / "receipt.json")
    assert receipt["status"] == "failed_or_unverified"
    assert "native_processes" not in receipt
    assert len(backend.spawned) == 1


def test_short_lived_probe_environment_is_not_required(fixture, monkeypatch):
    paths, expected, path, value, request, backend = fixture
    plan = app.prepare_launch(request, backend)
    first = True
    def role(identity):
        nonlocal first
        if first:
            first = False
            return "exited-native-command"
        return "app-server"
    backend.native_role = role
    original = backend.selected_environment
    environment_calls = []
    def environment(identity):
        environment_calls.append(identity["pid"])
        return original(identity)
    backend.selected_environment = environment
    result = app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    assert result["native_processes"][0]["native_role"] == "app-server"
    assert environment_calls == [101, 102]
    assert len(backend.spawned) == 1


def test_browser_profile_argument_is_derived_from_validated_frontend(fixture):
    paths, expected, path, value, request, backend = fixture
    request["app_args"] = ["--force-renderer-accessibility"]
    request_before = copy.deepcopy(request)
    plan = app.prepare_launch(request, backend)
    result = app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    assert request == request_before
    assert backend.spawned[0][1] == ["--user-data-dir=" + str(paths["frontend"]), "--force-renderer-accessibility"]
    assert result["app_profile_arguments"] == ["--user-data-dir=" + str(paths["frontend"])]


@pytest.mark.parametrize("observed", [[], ["--user-data-dir=foreign"], ["--user-data-dir=foreign", "--user-data-dir=second"]])
def test_missing_or_changed_browser_override_is_not_accepted(fixture, observed):
    paths, expected, path, value, request, backend = fixture
    plan = app.prepare_launch(request, backend)
    backend.profile_arguments = lambda identity: observed
    with pytest.raises(app.LaunchError, match="controlled browser profile"):
        app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    assert len(backend.spawned) == 1
    receipt = app.load_json(Path(plan["run_dir"]) / "receipt.json")
    assert receipt["status"] == "failed_or_unverified"

@pytest.mark.parametrize("subcommand", ["generate-ts", "generate-json-schema", "generate-internal-json-schema", "daemon", "proxy", "--help", "--version"])
def test_app_server_generation_and_diagnostic_commands_are_not_backends(subcommand):
    assert not app.is_app_server_command(["codex.exe", "-c", "features.code_mode_host=true", "app-server", subcommand])


def test_config_value_matching_subcommand_is_not_a_generator():
    assert app.is_app_server_command(["codex.exe", "app-server", "-c", "generate-ts"])


def test_git_fallback_preserves_existing_path_and_does_not_mutate_parent():
    original = {"Path": r"D:\ExistingGit\cmd;C:\Windows\System32;;", "KEEP": "value"}
    before = dict(original)
    dependency = {"status": "available", "path": r"C:\Program Files\Git\cmd\git.exe",
                  "directory": r"C:\Program Files\Git\cmd", "version": "git version fixture"}
    child, receipt = app.git_child_environment(original, dependency)
    assert original == before
    assert child["Path"] == before["Path"] + ";" + dependency["directory"]
    assert "PATH" not in child and child["KEEP"] == "value"
    assert child["Path"].split(";")[0] == r"D:\ExistingGit\cmd"
    assert receipt["path_appended"] and receipt["child_path_contains"]
    assert not receipt["effective_git_inferred"]
    assert receipt["worktree_gate"] == "pending_app_validation"
    assert before["Path"] not in json.dumps(receipt)


@pytest.mark.parametrize("previous", [None, "", r'C:\Existing\bin;"c:/program files/git/cmd/"'])
def test_git_path_absent_empty_and_already_present(previous):
    dependency = {"status": "available", "path": r"C:\Program Files\Git\cmd\git.exe",
                  "directory": r"C:\Program Files\Git\cmd", "version": "git version fixture"}
    parent = {} if previous is None else {"Path": previous}
    child, receipt = app.git_child_environment(parent, dependency)
    assert app.path_contains_directory(child, dependency["directory"])
    if previous:
        assert child == parent and receipt["path_appended"] is False
    else:
        assert child["PATH" if previous is None else "Path"] == dependency["directory"]
    assert parent == ({} if previous is None else {"Path": previous})


def test_missing_git_is_diagnostic_without_path_change_or_false_gate(fixture):
    paths, expected, path, value, request, backend = fixture
    checked = app.validate_request(request, backend)
    assert checked["git_dependency"]["status"] == "unavailable"
    original = {"PATH": r"D:\existing\bin"}
    child, receipt = app.git_child_environment(original, checked["git_dependency"])
    assert child == original
    assert receipt["child_path_contains"] is None
    assert receipt["worktree_gate"] == "pending_app_validation"
    plan = app.prepare_launch(request, backend)
    launched = app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    assert launched["status"] == "started_expected_backend"
    assert launched["git_dependency"]["status"] == "unavailable"
    assert not launched["app_runtime_acceptance"]


def test_trusted_git_ignores_workspace_and_environment_candidates(tmp_path, monkeypatch):
    untrusted = tmp_path / "workspace"
    (untrusted / "Git/cmd").mkdir(parents=True)
    (untrusted / "Git/cmd/git.exe").write_bytes(b"untrusted never execute")
    monkeypatch.chdir(untrusted)
    monkeypatch.setenv("ProgramFiles", str(untrusted))
    monkeypatch.setenv("PATH", str(untrusted / "Git/cmd"))
    calls = []
    result = app.trusted_git_from_roots([tmp_path / "missing-known-folder"], lambda p: calls.append(p))
    assert result["status"] == "unavailable"
    assert result["path"] is None and not calls


def test_trusted_git_records_actual_probe_and_hash(tmp_path):
    root = tmp_path / "ProgramFiles"
    exe = root / "Git/cmd/git.exe"; exe.parent.mkdir(parents=True); exe.write_bytes(b"fixture")
    calls = []
    def probe(path):
        calls.append(path)
        return "git version 2.56.0.windows.3"
    result = app.trusted_git_from_roots([root], probe)
    assert result["path"] == str(exe.resolve())
    assert result["directory"] == str(exe.parent.resolve())
    assert result["version"] == "git version 2.56.0.windows.3"
    assert result["sha256"] == app.sha(exe) and calls == [exe.resolve()]
    assert result["worktree_gate"] == "pending_app_validation"


@pytest.mark.parametrize("failure", ["timeout", "invalid-output", "replaced"])
def test_git_probe_failure_is_explicit_and_no_raw_output(tmp_path, failure):
    import subprocess
    root = tmp_path / "ProgramFiles"
    exe = root / "Git/cmd/git.exe"; exe.parent.mkdir(parents=True); exe.write_bytes(b"fixture")
    def probe(path):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("git", 5, output=b"never-log")
        if failure == "invalid-output":
            return "never-log arbitrary subprocess output"
        path.write_bytes(b"changed during probe")
        return "git version 2.56.0.windows.3"
    result = app.trusted_git_from_roots([root], probe)
    assert result["status"] == "probe_failed" and result["version"] is None
    assert "never-log" not in json.dumps(result)
    child, receipt = app.git_child_environment({"PATH": "keep"}, result)
    assert child == {"PATH": "keep"}
    assert receipt["worktree_gate"] == "pending_app_validation"


def test_git_installation_junction_cannot_escape_known_folder(tmp_path):
    import os, subprocess
    root = tmp_path / "ProgramFiles"; root.mkdir()
    outside = tmp_path / "workspace"; (outside / "cmd").mkdir(parents=True)
    (outside / "cmd/git.exe").write_bytes(b"untrusted never execute")
    alias = root / "Git"
    if os.name == "nt":
        command = Path(os.environ["SystemRoot"]) / "System32/cmd.exe"
        result = subprocess.run([str(command), "/d", "/c", "mklink", "/J", str(alias), str(outside)],
                                capture_output=True, timeout=10)
        assert result.returncode == 0
    else:
        alias.symlink_to(outside, target_is_directory=True)
    calls = []
    try:
        result = app.trusted_git_from_roots([root], lambda p: calls.append(p))
        assert result["status"] == "unavailable" and result["rejected_redirects"] == 1
        assert not calls
    finally:
        alias.rmdir() if os.name == "nt" else alias.unlink()


@pytest.mark.parametrize("observed", [True, False])
def test_git_is_scoped_to_new_app_child_and_receipt_is_not_worktree_acceptance(fixture, monkeypatch, observed):
    import os
    paths, expected, path, value, request, backend = fixture
    dependency = {"status": "available", "path": r"C:\Program Files\Git\cmd\git.exe",
                  "directory": r"C:\Program Files\Git\cmd", "version": "git version fixture",
                  "sha256": "b" * 64}
    backend.trusted_git = lambda: dict(dependency)
    backend.git_path_contains = lambda identity, directory: observed
    monkeypatch.setenv("PATH", r"D:\ExistingGit\cmd;C:\Windows\System32")
    before = dict(os.environ)
    plan = app.prepare_launch(request, backend)
    receipt = app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    assert dict(os.environ) == before
    spawned = backend.spawned[0][2]
    assert spawned["PATH"] == before["PATH"] + ";" + dependency["directory"]
    recorded = receipt["git_dependency"]
    assert recorded["path"] == dependency["path"] and recorded["version"] == dependency["version"]
    assert recorded["observed_app_path_contains"] is observed
    assert recorded["worktree_gate"] == "pending_app_validation"
    assert receipt["app_runtime_acceptance"] is False
    assert before["PATH"] not in json.dumps(receipt)
    if not observed:
        assert recorded["diagnostic"] == "App Git PATH inheritance is unverified"


@pytest.mark.parametrize('bad',[None,True,'', 'A'*64,'0'*63, '0'*65])
def test_present_invalid_expected_config_checksum_is_not_legacy_omission(fixture,bad):
    paths,expected,path,value,request,backend=fixture
    request['expected_config_sha256']=bad
    with pytest.raises(app.LaunchError,match='configuration checksum'):app.prepare_launch(request,backend)
    assert not backend.spawned and not paths['launches'].exists()


def test_pinned_config_is_checked_again_inside_existing_one_shot_protocol(fixture):
    paths,expected,path,value,request,backend=fixture
    request['expected_config_sha256']=app.sha(paths['home']/'config.toml')
    plan=app.prepare_launch(request,backend)
    config=paths['home']/'config.toml';config.write_text(config.read_text()+'# different installed configuration\n')
    with pytest.raises(app.LaunchError,match='configuration changed'):app.inside_launch(Path(plan['plan']),plan['plan_sha256'],backend)
    assert not backend.spawned
    with pytest.raises(FileExistsError):app.inside_launch(Path(plan['plan']),plan['plan_sha256'],backend)


def test_real_direct_edge_failure_cannot_mark_initial_backend_ready(fixture,monkeypatch):
    from test_app_process_edges import Graph
    paths,_,_,_,request,backend=fixture
    graph=Graph(monkeypatch);graph.rows[20].update(created=300.0,ppid=999)
    backend.descendants=lambda root:graph.backend.descendants(graph.root)
    plan=app.prepare_launch(request,backend)
    with pytest.raises(app.LaunchError,match="direct parent"):app.inside_launch(plan["plan"],plan["plan_sha256"],backend)
    receipt=app.load_json(Path(plan["run_dir"])/"receipt.json")
    assert receipt["status"]=="failed_or_unverified" and "native_processes" not in receipt
    assert len(backend.spawned)==1

def test_initial_backend_poll_retries_only_naturally_gone_child(fixture,monkeypatch):
    paths,_,_,_,request,backend=fixture
    original=backend.descendants;calls=[]
    def observed(root):
        calls.append(root["pid"])
        if len(calls)==1:raise app.psutil.NoSuchProcess(99999)
        return original(root)
    backend.descendants=observed
    plan=app.prepare_launch(request,backend)
    result=app.inside_launch(plan["plan"],plan["plan_sha256"],backend)
    assert result["status"]=="started_expected_backend" and len(calls)==2 and len(backend.spawned)==1
def test_initial_backend_poll_does_not_retry_pid_reuse_or_unknown_edge(fixture):
    paths,_,_,_,request,backend=fixture;calls=[]
    def observed(root):
        calls.append(root["pid"]);raise app.LaunchError("App child PID was reused")
    backend.descendants=observed
    plan=app.prepare_launch(request,backend)
    with pytest.raises(app.LaunchError,match="PID was reused"):app.inside_launch(plan["plan"],plan["plan_sha256"],backend)
    assert len(calls)==1 and len(backend.spawned)==1
    assert app.load_json(Path(plan["run_dir"])/"receipt.json")["status"]=="failed_or_unverified"
def test_natural_child_disappearance_does_not_allow_original_owner_loss(fixture):
    paths,_,_,_,request,backend=fixture;calls=[]
    def observed(root):
        calls.append(root["pid"]);raise app.psutil.NoSuchProcess(99999)
    backend.descendants=observed;backend.live=lambda root:False
    plan=app.prepare_launch(request,backend)
    with pytest.raises(app.LaunchError,match="original App exited"):app.inside_launch(plan["plan"],plan["plan_sha256"],backend)
    assert len(calls)==1


def test_launch_uses_bound_control_plane_while_native_owns_workloads(fixture):
    _,_,_,_,request,backend=fixture;broad=backend.descendants;calls=[]
    def bounded(root,**binding):
        calls.append(binding)
        return broad(root)
    backend.native_control_plane=bounded
    def reject_tree(root):raise app.LaunchError("unexpected owner/session in App process tree")
    backend.descendants=reject_tree
    plan=app.prepare_launch(request,backend)
    receipt=app.inside_launch(plan["plan"],plan["plan_sha256"],backend)
    assert receipt["status"]=="started_expected_backend"
    assert receipt["validation_scope"]=="app_frontend_and_verified_native_backends"
    assert receipt["native_workload_scope"]=="owned_by_native_runtime_not_enumerated"
    assert calls[0]["expected_sha256"]==app.sha(Path(request["native_manifest"]).parent/"codex.exe")


def _windows_security_bytes(path):
    # No windows-qa/pywin32 dependency in the ordinary test extra.
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileSecurityW.restype = wintypes.BOOL
    needed = wintypes.DWORD()
    assert not api.GetFileSecurityW(str(path), 7, None, 0, ctypes.byref(needed))
    assert ctypes.get_last_error() == 122 and needed.value > 0
    data = ctypes.create_string_buffer(needed.value)
    assert api.GetFileSecurityW(str(path), 7, data, needed.value, ctypes.byref(needed))
    return data.raw[:needed.value]


def test_launch_refuses_foreign_home_owner_before_creating_launch_receipts(fixture):
    paths, expected, path, value, request, backend = fixture
    backend.home_owner_mismatch = True
    before = (paths["home"] / "config.toml").read_bytes()
    with pytest.raises(app.LaunchError, match="home owner differs"):
        app.prepare_launch(request, backend)
    assert not backend.spawned and not paths["launches"].exists()
    assert (paths["home"] / "config.toml").read_bytes() == before


@pytest.mark.skipif(__import__("os").name != "nt", reason="actual Windows directory owner handles")
def test_actual_home_guard_creates_user_owned_directory_and_preserves_existing_acl(tmp_path):
    import os
    backend = app.WindowsBackend()
    owner = backend.identity(os.getpid())
    path = tmp_path / "dedicated-home"
    if owner["elevated"]:
        # A hosted/admin test process must not manufacture a BA-owned profile.
        with pytest.raises(app.LaunchError, match="non-elevated account"):
            with backend.home_guard(path, owner["sid"], create=True):
                pytest.fail("elevated profile creation admitted")
        assert not path.exists()
        return
    with backend.home_guard(path, owner["sid"], create=True) as first:
        assert first["created"] and first["owner_sid"] == owner["sid"]
        (path / "existing-session.txt").write_text("preserve this state")
    before = _windows_security_bytes(path)
    with backend.home_guard(path, owner["sid"], create=True) as second:
        assert not second["created"]
    assert _windows_security_bytes(path) == before
    assert (path / "existing-session.txt").read_text() == "preserve this state"


@pytest.mark.skipif(__import__("os").name != "nt", reason="actual Windows owner mismatch is read-only")
def test_actual_home_guard_wrong_expected_user_does_not_reown_existing_directory(tmp_path):
    import os
    backend = app.WindowsBackend()
    owner = backend.identity(os.getpid())
    path = tmp_path / "existing-home"; path.mkdir()
    (path / "existing-session.txt").write_text("preserve this state")
    before = _windows_security_bytes(path)
    foreign = "S-1-5-21-1-2-3-9999"
    assert foreign != owner["sid"]
    with pytest.raises(app.LaunchError, match="home owner differs"):
        with backend.home_guard(path, foreign):
            pytest.fail("foreign home admitted")
    assert _windows_security_bytes(path) == before
    assert (path / "existing-session.txt").read_text() == "preserve this state"


@pytest.mark.skipif(__import__("os").name != "nt", reason="actual Windows junction metadata")
@pytest.mark.parametrize("redirect_parent", [False, True])
def test_actual_home_guard_rejects_junction_without_touching_target(tmp_path, redirect_parent):
    import os
    import subprocess
    backend = app.WindowsBackend()
    sid = backend.identity(os.getpid())["sid"]
    target = tmp_path / "target"; target.mkdir()
    (target / "existing-state.txt").write_text("preserve this state")
    link = tmp_path / "redirect"
    result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    before = _windows_security_bytes(target)
    try:
        path = link / "new-home" if redirect_parent else link
        with pytest.raises(app.LaunchError, match="redirected"):
            with backend.home_guard(path, sid, create=True):
                pytest.fail("redirected profile admitted")
        assert _windows_security_bytes(target) == before
        assert sorted(x.name for x in target.iterdir()) == ["existing-state.txt"]
        assert (target / "existing-state.txt").read_text() == "preserve this state"
    finally:
        # Remove only the test-created junction, never recurse through its target.
        link.rmdir()


@pytest.mark.parametrize("damage", ["missing", "unreadable", "checksum"])
def test_cleanup_only_admits_damaged_unused_helper_without_weakening_app_guard(fixture, monkeypatch, damage):
    paths, expected, path, manifest, request, backend = fixture
    helper = Path(manifest["outputs"][2]["path"])
    original_sha = app.sha
    if damage == "missing":
        helper.unlink()
    elif damage == "unreadable":
        def unreadable(file):
            if Path(file) == helper:
                raise PermissionError("quarantined fixture")
            return original_sha(file)
        monkeypatch.setattr(app, "sha", unreadable)
    else:
        helper.write_bytes(b"changed unused fixture")
    result = app.verify_bundle(path, request["native_manifest_sha256"], expected, cleanup_only=True)
    report = result["cleanup_verification"]
    assert report["scope"] == "cleanup_cli_only" and report["full_bundle_verified"] is False
    assert report["unexecuted_components"][helper.name] == {
        "status": {"missing": "missing", "unreadable": "unreadable", "checksum": "checksum_mismatch"}[damage],
        "required_for_cleanup": False}
    assert "codex.exe" not in report["unexecuted_components"]
    with pytest.raises((app.LaunchError, OSError)):
        app.verify_bundle(path, request["native_manifest_sha256"], expected)
    with pytest.raises((app.LaunchError, OSError)):
        app.validate_request(request, backend)
    assert not backend.spawned


@pytest.mark.parametrize("damage", ["missing", "unreadable", "checksum", "size", "outside", "redirected"])
def test_cleanup_only_still_requires_exact_executable(fixture, monkeypatch, damage):
    paths, expected, path, manifest, request, backend = fixture
    executable = Path(manifest["outputs"][0]["path"])
    original_sha = app.sha
    if damage == "missing":
        executable.unlink()
    elif damage == "unreadable":
        def unreadable(file):
            if Path(file) == executable:
                raise PermissionError("unreadable CLI")
            return original_sha(file)
        monkeypatch.setattr(app, "sha", unreadable)
    elif damage == "checksum":
        executable.write_bytes(b"x" * executable.stat().st_size)
    elif damage == "size":
        executable.write_bytes(b"changed")
    elif damage == "outside":
        manifest["outputs"][0]["path"] = str(path.parent.parent / "codex.exe")
        path.write_text(json.dumps(manifest))
        request["native_manifest_sha256"] = app.sha(path)
    else:
        real = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda file: file == executable or real(file))
    with pytest.raises((app.LaunchError, OSError)):
        app.verify_bundle(path, request["native_manifest_sha256"], expected, cleanup_only=True)


@pytest.mark.parametrize("mutation", ["sha-shape", "bool-size", "negative-size", "outside", "duplicate", "missing-declaration", "not-object"])
def test_cleanup_only_never_relaxes_unused_component_manifest_schema(fixture, mutation):
    paths, expected, path, manifest, request, backend = fixture
    row = manifest["outputs"][2]
    if mutation == "sha-shape": row["sha256"] = "not-a-checksum"
    elif mutation == "bool-size": row["size"] = True
    elif mutation == "negative-size": row["size"] = -1
    elif mutation == "outside": row["path"] = str(path.parent.parent / row["name"])
    elif mutation == "duplicate": manifest["outputs"].append(dict(row))
    elif mutation == "missing-declaration": manifest["outputs"].pop(2)
    else: manifest["outputs"][2] = "invalid"
    path.write_text(json.dumps(manifest))
    with pytest.raises(app.LaunchError):
        app.verify_bundle(path, app.sha(path), expected, cleanup_only=True)


def test_cleanup_only_ignores_validly_declared_fake_helper_hash_but_reports_mismatch(fixture):
    paths, expected, path, manifest, request, backend = fixture
    manifest["outputs"][2]["sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    result = app.verify_bundle(path, app.sha(path), expected, cleanup_only=True)
    assert result["cleanup_verification"]["unexecuted_components"][manifest["outputs"][2]["name"]]["status"] == "checksum_mismatch"
    with pytest.raises(app.LaunchError):
        app.verify_bundle(path, app.sha(path), expected)


def test_cleanup_unused_reparse_is_diagnostic_without_following_target(fixture, monkeypatch):
    paths, expected, path, manifest, request, backend = fixture
    helper = Path(manifest["outputs"][2]["path"])
    real_reparse = app._is_link_or_reparse
    monkeypatch.setattr(app, "_is_link_or_reparse", lambda file: Path(file) == helper or real_reparse(file))
    original_sha = app.sha
    def no_read(file):
        if Path(file) == helper: pytest.fail("followed unused reparse")
        return original_sha(file)
    monkeypatch.setattr(app, "sha", no_read)
    result = app.verify_bundle(path, request["native_manifest_sha256"], expected, cleanup_only=True)
    assert result["cleanup_verification"]["unexecuted_components"][helper.name]["status"] == "redirected"


def _foreign_app(backend, *, home=None, frontend=None, browser=None):
    """Foreign-process metadata fixture; never starts or controls any process."""
    identity = {**backend.identity(999), "created":100.0}
    state = {"identity":copy.deepcopy(identity), "environment":dict.fromkeys(app.CONTROLLED),
             "arguments":[] if browser is None else ["--user-data-dir=" + browser],
             "exists":True}
    state["environment"].update(CODEX_HOME=home,CODEX_ELECTRON_USER_DATA_PATH=frontend)
    original_identity = backend.identity
    original_environment = backend.selected_environment
    original_arguments = backend.profile_arguments
    def current(pid):
        if pid != 999: return original_identity(pid)
        if not state["exists"]: raise app.psutil.NoSuchProcess(pid)
        return copy.deepcopy(state["identity"])
    backend.identity = current
    backend.selected_environment = lambda row: copy.deepcopy(state["environment"]) if row["pid"] == 999 else original_environment(row)
    backend.profile_arguments = lambda row: list(state["arguments"]) if row["pid"] == 999 else original_arguments(row)
    backend.existing = [copy.deepcopy(identity)]
    return state


@pytest.mark.parametrize("override",["stock-defaults","native-home","frontend","browser","all"])
def test_distinct_foreign_App_can_coexist_without_adoption_or_control(fixture,override):
    paths,_,_,_,request,backend = fixture
    other = paths["home"].parent / "foreign-home"
    state = _foreign_app(backend,
        home=str(other) if override in ("native-home","all") else None,
        frontend=str(other / "electron") if override in ("frontend","all") else None,
        browser=str(other / "electron") if override in ("browser","all") else None)
    before = copy.deepcopy(state)
    plan = app.prepare_launch(request,backend)
    result = app.inside_launch(Path(plan["plan"]),plan["plan_sha256"],backend)
    assert result["status"] == "started_expected_backend"
    assert result["app"]["pid"] == 101 and result["native_processes"][0]["pid"] == 102
    assert len(backend.spawned) == 1 and state == before
    assert 999 not in [row["pid"] for row in result["native_processes"]]


@pytest.mark.parametrize("selector",["home","frontend","browser"])
@pytest.mark.parametrize("alias",["exact","casefold","dotdot"])
def test_foreign_App_same_native_or_Electron_or_browser_profile_is_denied(fixture,selector,alias):
    paths,_,_,_,request,backend = fixture
    value = str(paths["home"] if selector == "home" else paths["frontend"])
    if alias == "casefold": value = value.swapcase()
    elif alias == "dotdot": value = str(Path(value) / "unused" / "..")
    _foreign_app(backend,**{selector:value})
    with pytest.raises(app.LaunchError,match="dedicated"):
        app.prepare_launch(request,backend)
    assert not backend.spawned and not paths["launches"].exists()


@pytest.mark.parametrize("arguments",[
    ["--user-data-dir"],["--user-data-dir",r"C:\foreign"],
    ["--user-data-dir="],["--user-data-dir=a","--user-data-dir=b"],
    ["--User-Data-Dir=C:\\foreign"],[None],
])
def test_foreign_App_ambiguous_browser_selectors_fail_closed(fixture,arguments):
    paths,_,_,_,request,backend = fixture
    state = _foreign_app(backend)
    state["arguments"] = arguments
    with pytest.raises(app.LaunchError):
        app.prepare_launch(request,backend)
    assert not backend.spawned


@pytest.mark.parametrize("fault",["unknown-key","missing-key","empty-home","relative-home","non-string","access-denied","reused-before","reused-after","owner-changed","unknown-exit"])
def test_foreign_App_unknown_or_reused_identity_never_authorizes_launch(fixture,fault):
    paths,_,_,_,request,backend = fixture
    state = _foreign_app(backend)
    if fault == "unknown-key":state["environment"]["UNRELATED"] = "never expose"
    elif fault == "missing-key":state["environment"].pop("CODEX_HOME")
    elif fault == "empty-home":state["environment"]["CODEX_HOME"] = ""
    elif fault == "relative-home":state["environment"]["CODEX_HOME"] = "relative-home"
    elif fault == "non-string":state["environment"]["CODEX_CLI_PATH"] = 1
    elif fault == "access-denied":
        def denied(row):raise app.psutil.AccessDenied(row["pid"])
        backend.selected_environment = denied
    elif fault == "reused-before":state["identity"]["created"] += 1
    elif fault == "owner-changed":state["identity"]["sid"] = "S-1-5-21-other"
    else:
        original = backend.profile_arguments
        def change(row):
            if fault == "reused-after":
                result = original(row);state["identity"]["created"] += 1;return result
            raise app.psutil.NoSuchProcess(row["pid"])
        backend.profile_arguments = change
    with pytest.raises(app.LaunchError):
        app.prepare_launch(request,backend)
    assert not backend.spawned


def test_foreign_App_natural_disappearance_is_confirmed_not_replaced(fixture):
    paths,_,_,_,request,backend = fixture
    state = _foreign_app(backend)
    original = backend.profile_arguments
    def vanished(row):
        state["exists"] = False
        raise app.psutil.NoSuchProcess(row["pid"])
    backend.profile_arguments = vanished
    result = app.validate_request(request,backend)
    assert result["environment"]["CODEX_HOME"] == str(paths["home"])
    assert not backend.spawned


def test_new_profile_conflict_is_rechecked_inside_original_launch_lock(fixture):
    paths,_,_,_,request,backend = fixture
    state = _foreign_app(backend)
    plan = app.prepare_launch(request,backend)
    state["arguments"] = ["--user-data-dir=" + str(paths["frontend"])]
    with pytest.raises(app.LaunchError,match="dedicated"):
        app.inside_launch(Path(plan["plan"]),plan["plan_sha256"],backend)
    assert not backend.spawned


def test_selected_environment_keeps_only_four_case_insensitive_selectors(monkeypatch):
    backend = object.__new__(app.WindowsBackend)
    backend._require_live = lambda *args:None
    class Process:
        def environ(self):
            return {"codex_home":r"C:\private","PATH":"unrelated secret fixture",
                    "CODEX_ELECTRON_USER_DATA_PATH":r"C:\frontend","OPENROUTER_API_KEY":"not returned"}
    monkeypatch.setattr(app.psutil,"Process",lambda pid:Process())
    values = backend.selected_environment({"pid":999})
    assert set(values) == app.CONTROLLED
    assert values["CODEX_HOME"] == r"C:\private"
    assert values["CODEX_CLI_PATH"] is None
    assert "not returned" not in json.dumps(values)


def test_selected_environment_rejects_ambiguous_Windows_case_aliases(monkeypatch):
    backend = object.__new__(app.WindowsBackend)
    backend._require_live = lambda *args:None
    class Process:
        def environ(self):return {"CODEX_HOME":r"C:\a","codex_home":r"C:\b"}
    monkeypatch.setattr(app.psutil,"Process",lambda pid:Process())
    with pytest.raises(app.LaunchError,match="ambiguous"):
        backend.selected_environment({"pid":999})

@pytest.mark.parametrize("arguments",[
    ["--user-data-dir"],["--user-data-dir",r"C:\profile"],
    [r"-user-data-dir=C:\profile"],[r"/user-data-dir=C:\profile"],
    [r"--USER-DATA-DIR=C:\profile"],["--user-data-dir-unknown=anything"],
])
def test_browser_selector_observation_retains_ambiguous_switch_aliases(monkeypatch,arguments):
    backend = object.__new__(app.WindowsBackend)
    backend._require_live = lambda *args:None
    class Process:
        def cmdline(self):return ["ChatGPT.exe",*arguments,"--type=renderer"]
    monkeypatch.setattr(app.psutil,"Process",lambda pid:Process())
    retained = backend.profile_arguments({"pid":999})
    assert retained == [arguments[0]]
