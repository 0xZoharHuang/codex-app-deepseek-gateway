from pathlib import Path
import json
import os
import subprocess
import sys
import time

import pytest
from codex_app_deepseek import cli


def test_home_lease_excludes_another_process_and_releases_on_exit(tmp_path):
    home = tmp_path / "gateway"
    home.mkdir()
    code = "from pathlib import Path; import sys; from codex_app_deepseek.cli import _service_lease,ServiceConfigError;\ntry:\n with _service_lease(Path(sys.argv[1])): print('acquired')\nexcept ServiceConfigError:\n print('locked');sys.exit(9)"
    with cli._service_lease(home):
        second = subprocess.run([sys.executable, "-I", "-c", code, str(home)], capture_output=True, timeout=10)
        assert second.returncode == 9 and second.stdout.strip() == b"locked"
    third = subprocess.run([sys.executable, "-I", "-c", code, str(home)], capture_output=True, timeout=10)
    assert third.returncode == 0 and third.stdout.strip() == b"acquired"
    assert (home / "service.lock").exists(), "Keep stable lock inode between owners"


def test_process_death_releases_only_its_home_lease(tmp_path):
    home = tmp_path / "gateway"
    home.mkdir()
    ready = home / "fixture-ready"
    code = "from pathlib import Path;import sys,time;from codex_app_deepseek.cli import _service_lease;\nwith _service_lease(Path(sys.argv[1])):\n Path(sys.argv[2]).write_text('ready');time.sleep(30)"
    child = subprocess.Popen([sys.executable, "-I", "-c", code, str(home), str(ready)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        end = time.monotonic() + 8
        while not ready.exists() and child.poll() is None and time.monotonic() < end:
            time.sleep(.02)
        assert ready.exists() and child.poll() is None
        with pytest.raises(cli.ServiceConfigError, match="already owns"):
            with cli._service_lease(home):
                pytest.fail("Live owner must retain the lease")
        child.terminate()  # Exact test child handle; never a process-name kill.
        child.wait(timeout=5)
        with cli._service_lease(home):
            assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=5)
        child.stderr.close()


def test_service_record_failure_preserves_previous_complete_record(tmp_path, monkeypatch):
    path = tmp_path / "service.json"
    before = b'{"pid": 1, "created": 42}'
    path.write_bytes(before)
    real_replace = os.replace
    def fail(source, target):
        if Path(target) == path:
            raise OSError("fixture atomic replacement failure")
        return real_replace(source, target)
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="fixture"):
        cli._write_service_record(tmp_path, {"pid": 2, "created": 84})
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".service-*.tmp"))


def test_service_record_replaces_atomically_and_has_no_temp_artifacts(tmp_path):
    expected = {"pid": 2, "created": 84, "host": "127.0.0.1"}
    cli._write_service_record(tmp_path, expected)
    assert json.loads((tmp_path / "service.json").read_text()) == expected
    assert not list(tmp_path.glob(".service-*.tmp"))


def test_lock_hardlink_is_not_touched(tmp_path):
    source = tmp_path / "unrelated-file"
    source.write_bytes(b"preserve")
    home = tmp_path / "gateway"
    home.mkdir()
    os.link(source, home / "service.lock")
    with pytest.raises(cli.ServiceConfigError, match="regular owned"):
        with cli._service_lease(home):
            pytest.fail("Hardlinked state must not be opened")
    assert source.read_bytes() == b"preserve"
