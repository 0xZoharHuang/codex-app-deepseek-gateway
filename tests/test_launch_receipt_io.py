
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import pytest
from codex_app_deepseek import app_config, app_delivery
from codex_app_deepseek import isolated_app as app
from test_isolated_app import fixture


def busy(code=5):
    error = PermissionError(13, "owned fixture reader is active")
    if code is not None:
        error.winerror = code
    return error


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement error contract")
@pytest.mark.parametrize("code", [5, 32, 33])
def test_receipt_transient_contention_retries_the_same_bytes_only(tmp_path, monkeypatch, code):
    writes, sleeps = [], []
    original = os.replace
    target = tmp_path / "receipt.json"
    error = busy(code)
    def replace(source, destination):
        if Path(destination) == target:
            writes.append((Path(source), Path(source).read_bytes()))
            if len(writes) < 4:
                raise error
        return original(source, destination)
    monkeypatch.setattr(app_config.os, "replace", replace)
    monkeypatch.setattr(app_config.time, "sleep", sleeps.append)
    app._save_receipt(target, {"status": "observing_backend", "replay_allowed": False})
    assert len(writes) == 4 and len(sleeps) == 3
    assert all(row == writes[0] for row in writes)
    assert json.loads(target.read_bytes())["replay_allowed"] is False


@pytest.mark.parametrize("code", [None, 2, 87])
def test_receipt_nonsharing_permission_errors_are_not_retried(tmp_path, monkeypatch, code):
    writes, sleeps = [], []
    error = busy(code)
    original = os.replace
    target = tmp_path / "receipt.json"
    def replace(source, destination):
        if Path(destination) == target:
            writes.append(source)
            raise error
        return original(source, destination)
    monkeypatch.setattr(app_config.os, "replace", replace)
    monkeypatch.setattr(app_config.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError) as caught:
        app._save_receipt(target, {"status": "observing_backend"})
    assert caught.value is error
    assert len(writes) == 1 and sleeps == []


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement error contract")
def test_receipt_persistent_contention_fails_after_bounded_io_retries(tmp_path, monkeypatch):
    writes, sleeps = [], []
    error = busy()
    target = tmp_path / "receipt.json"
    original = os.replace
    def replace(source, destination):
        if Path(destination) == target:
            writes.append(Path(source))
            raise error
        return original(source, destination)
    monkeypatch.setattr(app_config.os, "replace", replace)
    monkeypatch.setattr(app_config.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError) as caught:
        app._save_receipt(target, {"status": "observing_backend"})
    assert caught.value is error
    assert len(writes) == 8 and len(set(writes)) == 1 and len(sleeps) == 7
    assert max(sleeps) <= .2 and sum(sleeps) < 1


def test_receipt_serialization_failure_precedes_any_write(tmp_path, monkeypatch):
    writes = []
    monkeypatch.setattr(app, "_atomic_write", lambda *args: writes.append(args))
    with pytest.raises(TypeError):
        app._save_receipt(tmp_path / "receipt.json", {"not_json": object()})
    assert writes == []


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement error contract")
def test_shared_atomic_write_has_only_one_bounded_retry_loop(tmp_path, monkeypatch):
    target = tmp_path / "config.toml"
    target.write_bytes(b"user_setting = 1\n")
    old = target.read_bytes()
    replace = os.replace
    calls = []
    def blocked(source, destination):
        if Path(destination) == target:
            calls.append((source, destination))
            raise busy()
        return replace(source, destination)
    monkeypatch.setattr(app_config.os, "replace", blocked)
    with pytest.raises(PermissionError):
        app_config._atomic_write(target, b"user_setting = 2\n")
    assert len(calls) == 8 and target.read_bytes() == old
    assert list(tmp_path.glob(".app-deepseek-*.tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement error contract")
@pytest.mark.parametrize("persistent", [False, True])
def test_receipt_contention_after_spawn_never_recreates_the_app(fixture, monkeypatch, persistent):
    paths, _, _, _, request, backend = fixture
    plan = app.prepare_launch(request, backend)
    original = os.replace
    attempts = []
    first_source = None
    def contention(source, destination):
        nonlocal first_source
        if Path(destination).name == "receipt.json":
            payload = json.loads(Path(source).read_bytes())
            if payload.get("status") == "observing_backend":
                first_source = first_source or Path(source)
                if Path(source) == first_source:
                    attempts.append(Path(source))
                    if persistent or len(attempts) == 1:
                        raise busy()
        return original(source, destination)
    monkeypatch.setattr(app_config.os, "replace", contention)
    monkeypatch.setattr(app_config.time, "sleep", lambda unused: None)
    if persistent:
        with pytest.raises(PermissionError):
            app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
        recorded = app.load_json(Path(plan["run_dir"]) / "receipt.json")
        assert recorded["status"] != "started_expected_backend"
        assert len(attempts) == 8
    else:
        recorded = app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
        assert recorded["status"] == "started_expected_backend"
        assert len(attempts) == 2
    assert len(backend.spawned) == 1 and recorded["replay_allowed"] is False
    with pytest.raises(FileExistsError):
        app.inside_launch(Path(plan["plan"]), plan["plan_sha256"], backend)
    assert len(backend.spawned) == 1


@pytest.mark.skipif(os.name != "nt", reason="real Windows file replacement contention")
def test_real_short_reader_collision_is_retried_without_changing_the_target_early(tmp_path, monkeypatch):
    target = tmp_path / "receipt.json"
    app.save(target, {"generation": "old"})
    before = target.read_bytes()
    held = target.open("rb")
    with pytest.raises(PermissionError):
        app.save(target, {"generation": "control_blocked"})
    assert target.read_bytes() == before
    failed = threading.Event()
    errors, attempts = [], []
    original_replace = os.replace
    def traced(source, destination):
        if Path(destination) == target:
            attempts.append(Path(source))
        try:
            return original_replace(source, destination)
        except PermissionError:
            if Path(destination) == target:
                assert target.read_bytes() == before
                failed.set()
            raise
    def release():
        try:
            assert failed.wait(5)
            time.sleep(.03)
            held.close()
        except BaseException as error:
            errors.append(error)
            held.close()
    worker = threading.Thread(target=release)
    worker.start()
    monkeypatch.setattr(app_config.os, "replace", traced)
    try:
        app._save_receipt(target, {"generation": "new"})
    finally:
        failed.set()
        held.close()
        worker.join(5)
    assert not worker.is_alive() and errors == []
    assert len(attempts) >= 2
    assert json.loads(target.read_bytes()) == {"generation": "new"}
    assert list(tmp_path.glob(".app-deepseek-*.tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="actual Windows PowerShell 5.1 receipt reader")
def test_powershell_reader_closes_before_downstream_pipeline_work(tmp_path):
    target = tmp_path / "receipt.json"
    app.save(target, {"generation": "old"})
    ready, release = tmp_path / "ready", tmp_path / "release"
    probe = tmp_path / "reader.ps1"
    probe.write_text(r'''param([string]$Source,[string]$InputPath,[string]$Ready,[string]$Release)
$ErrorActionPreference="Stop"
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw "production script parse failed"}
$definition=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq "Read-OwnedReceipt"},$true)
if(!$definition){throw "production receipt reader missing"}
Invoke-Expression $definition.Extent.Text
Read-OwnedReceipt $InputPath | ForEach-Object {
 if($_.generation -ne "old"){throw "reader did not capture old snapshot"}
 [IO.File]::WriteAllText($Ready,"ready")
 $deadline=[DateTime]::UtcNow.AddSeconds(10)
 while(!(Test-Path -LiteralPath $Release) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 10}
}
''', encoding="utf-8")
    source = app_delivery.assets()[1]
    child = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-File", str(probe), str(source), str(target), str(ready), str(release)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), "reader failed before the controlled downstream hold"
        # Explicit single attempt: the reader must already have released its handle.
        app_config._atomic_write(target, b'{"generation": "new"}', retry_transient=False)
        assert json.loads(target.read_bytes()) == {"generation": "new"}
    finally:
        release.write_text("release")
        stdout, stderr = child.communicate(timeout=15)
    assert child.returncode == 0, (stdout.decode(errors="replace"), stderr.decode(errors="replace"))
