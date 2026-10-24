from __future__ import annotations
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import psutil
import pytest
from codex_app_deepseek import lifecycle as lc
from test_lifecycle import bundle, process_fixture, installed


def test_verified_standard_base_image_keeps_private_command_and_stops_only_owner(bundle):
    process, factory = process_fixture(bundle)
    process.image = str(bundle.python)
    assert process.arguments[0] != process.image
    assert lc.stop(bundle.install, process_factory=factory, remove_task=False)["status"] == "stopped"
    assert process.terminated and not (bundle.data / "service.json").exists()


@pytest.mark.parametrize("mismatch", ["base_path", "base_bytes", "venv_cfg", "argv0", "isolation", "port", "birth"])
def test_redirected_service_still_rejects_wrong_runtime_or_private_identity(bundle, mismatch):
    process, factory = process_fixture(bundle)
    process.image = str(bundle.python)
    directory = Path(lc._active(bundle.install)[0])
    if mismatch == "base_path":
        other = bundle.root / "another-python.exe"; other.write_bytes(bundle.python.read_bytes())
        process.image = str(other)
    elif mismatch == "base_bytes":
        bundle.python.write_bytes(b"changed base runtime")
    elif mismatch == "venv_cfg":
        cfg = directory / "venv/pyvenv.cfg"
        cfg.write_text(cfg.read_text().replace("include-system-site-packages = false", "include-system-site-packages = true"))
    elif mismatch == "argv0":
        process.arguments[0] = str(bundle.python)
    elif mismatch == "isolation":
        process.arguments[1] = "-E"
    elif mismatch == "port":
        process.arguments[-1] = "42818"
    else:
        process.created += 1
    with pytest.raises(lc.LifecycleError):
        lc.stop(bundle.install, process_factory=factory, remove_task=False)
    assert not process.terminated and (bundle.data / "service.json").exists()


@pytest.mark.parametrize("change", ["missing", "false_hash", "different_base"])
def test_installation_requires_exact_runtime_proof_without_legacy_guess(bundle, change):
    result, _ = installed(bundle)
    directory = Path(result["release_dir"])
    receipt = lc._json(directory / "installation.json")
    if change == "missing":
        receipt.pop("runtime")
    elif change == "false_hash":
        receipt["runtime"]["base_executable"]["sha256"] = "0" * 64
    else:
        other = bundle.root / "other-base.exe"; other.write_bytes(bundle.python.read_bytes())
        receipt["bootstrap_python"] = str(other)
    with pytest.raises(lc.LifecycleError, match="runtime|provenance"):
        lc._verify_installation(directory, receipt)


def test_health_rechecks_private_identity_before_any_credential_probe(bundle, monkeypatch):
    process, _ = process_fixture(bundle)
    process.image = str(bundle.python)
    process.arguments[1] = "-E"
    process.net_connections = lambda kind: [SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(ip="127.0.0.1", port=42817))]
    monkeypatch.setattr(lc, "health", lambda port: pytest.fail("Invalid identity must not read a health credential"))
    with pytest.raises(lc.LifecycleError, match="isolated"):
        lc._owned_health(process, bundle.data, 42817)


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows account translation")
def test_scheduler_resolves_original_sid_and_normalized_accounts_and_rejects_foreign_owner():
    code = lc._TASK_PRINCIPAL_SID + r"""
$ErrorActionPreference='Stop'
$id=[Security.Principal.WindowsIdentity]::GetCurrent();$sid=$id.User.Value
$definition=[pscustomobject]@{RegistrationInfo=[pscustomobject]@{Description='owned marker'};Principal=[pscustomobject]@{UserId=$sid;LogonType=3;RunLevel=0}}
$result=@{}
$result.sid=Test-CadsgTaskOwner $definition 'owned marker' $sid
$definition.Principal.UserId=$id.Name
$result.qualified_account=Test-CadsgTaskOwner $definition 'owned marker' $sid
$definition.Principal.UserId=$env:USERNAME
$result.normalized_account=Test-CadsgTaskOwner $definition 'owned marker' $sid
$definition.Principal.UserId='S-1-5-18'
$result.wrong_owner_rejected= -not (Test-CadsgTaskOwner $definition 'owned marker' $sid)
$definition.Principal.UserId=$sid;$definition.Principal.RunLevel=1
$result.elevated_rejected= -not (Test-CadsgTaskOwner $definition 'owned marker' $sid)
$definition.Principal.RunLevel=0;$definition.Principal.LogonType=1
$result.password_logon_rejected= -not (Test-CadsgTaskOwner $definition 'owned marker' $sid)
$definition.Principal.LogonType=3;$definition.Principal.UserId=$sid
$result.wrong_description_rejected= -not (Test-CadsgTaskOwner $definition 'other marker' $sid)
try {Resolve-CadsgTaskSid 'CADSG-unresolvable-fixture-account-79d410'|Out-Null;$result.unresolvable_rejected=$false}
catch {$result.unresolvable_rejected=$true}
$result|ConvertTo-Json -Compress
"""
    result = json.loads(lc._powershell(code).stdout)
    assert len(result) == 8 and all(value is True for value in result.values()), result


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows venv redirector image identity")
def test_actual_private_venv_process_accepts_verified_base_and_owned_listener(tmp_path, monkeypatch):
    directory = tmp_path / "managed" / "releases" / ("b" * 20)
    directory.mkdir(parents=True)
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    venv = directory / "venv"
    lc._run([str(base), "-I", "-m", "venv", "--copies", "--without-pip", str(venv)], timeout=30)
    runtime = venv / "Scripts/python.exe"
    proof = lc._runtime_proof(directory, base)
    assert proof["venv_executable"]["sha256"] == lc.digest(runtime)
    (directory / "installation.json").write_text(json.dumps({"bootstrap_python": str(base), "runtime": proof}))
    package = venv / "Lib/site-packages/codex_app_deepseek"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text(
        "import json,os,socket,sys\nfrom pathlib import Path\n"
        "home=Path(sys.argv[3]);port=int(sys.argv[5])\n"
        "sock=socket.socket();sock.bind(('127.0.0.1',port));sock.listen(1)\n"
        "(home/'ready.json').write_text(json.dumps({'pid':os.getpid(),'prefix':sys.prefix,'isolated':sys.flags.isolated}))\n"
        "sys.stdin.buffer.read();sock.close()\n"
    )
    data = tmp_path / "private-data"; data.mkdir()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0)); port = reservation.getsockname()[1]
    argv = [str(runtime), "-I", "-m", "codex_app_deepseek.cli", "serve", "--home", str(data), "--port", str(port)]
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process = psutil.Process(child.pid); birth = process.create_time()
    try:
        deadline = time.monotonic() + 10
        while not (data / "ready.json").exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert (data / "ready.json").exists(), "Private fixture did not become ready"
        ready = json.loads((data / "ready.json").read_text())
        assert ready["isolated"] == 1 and lc._same_runtime_path(ready["prefix"], venv)
        # The launcher PID belongs to the venv redirector. The actual interpreter
        # records its own PID, exactly as cli.serve writes service.json.
        launcher_pid = child.pid
        process = psutil.Process(ready["pid"]); birth = process.create_time()
        assert process.pid != launcher_pid
        # This is the observed Windows redirector behavior, not a mocked executable.
        assert lc._same_runtime_path(process.exe(), base)
        assert not lc._same_runtime_path(process.exe(), runtime)
        record = {"pid": process.pid, "created": birth, "executable": str(runtime), "port": port, "host": "127.0.0.1"}
        (data / "service.json").write_text(json.dumps(record))
        owned, record_sha = lc._owned_process(directory, data)
        assert owned.pid == process.pid and record_sha == lc.digest(data / "service.json")
        calls = []
        monkeypatch.setattr(lc, "health", lambda selected: calls.append(selected) or {"status": "ready"})
        assert lc._owned_health(owned, data, port) == {"status": "ready"}
        assert calls == [port]
        (data / "identity-proof.json").write_text(json.dumps({
            "launcher_pid": launcher_pid, "actual_pid": process.pid, "actual_birth": birth,
            "actual_image": process.exe(), "private_argv": process.cmdline(),
            "runtime_proof": proof, "owned_process_accepted": True, "owned_listener_accepted": True,
            "model_requests": 0, "real_gateway_started": False,
        }, indent=2))
        record["created"] += 1
        (data / "service.json").write_text(json.dumps(record))
        with pytest.raises(lc.LifecycleError, match="creation"):
            lc._owned_process(directory, data)
        assert child.poll() is None
    finally:
        # End only this newly created exact fixture through its private stdin.
        try:
            child.communicate(input=b"", timeout=10)
        except subprocess.TimeoutExpired:
            if process.is_running() and abs(process.create_time() - birth) < .001:
                process.terminate()
            child.communicate(timeout=10)
    assert child.returncode == 0
