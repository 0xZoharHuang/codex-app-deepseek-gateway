"""Release output must be independent of source permissions and later writes."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_gateway_release.py"
spec = importlib.util.spec_from_file_location("gateway_release_builder_test", SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dependency_is_new_file_not_link_and_input_mutation_cannot_change_bundle(tmp_path):
    original, output = tmp_path / "original.whl", tmp_path / "output.whl"
    original.write_bytes(b"pinned wheel bytes")
    expected = digest(original)
    before = original.stat()
    builder.copy_dependency(original, output, expected)
    assert digest(output) == expected
    assert not original.samefile(output) and output.stat().st_nlink == 1
    assert (original.stat().st_ino, original.stat().st_nlink) == (before.st_ino, before.st_nlink)
    original.write_bytes(b"later input mutation")
    assert digest(output) == expected


def test_existing_output_is_not_overwritten(tmp_path):
    original, output = tmp_path / "original.whl", tmp_path / "output.whl"
    original.write_bytes(b"pinned");output.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        builder.copy_dependency(original, output, digest(original))
    assert output.read_bytes() == b"existing"


def test_bad_pin_creates_no_output(tmp_path):
    original, output = tmp_path / "original.whl", tmp_path / "output.whl"
    original.write_bytes(b"wrong bytes")
    with pytest.raises(ValueError, match="changed before"):
        builder.copy_dependency(original, output, "0" * 64)
    assert not output.exists()


def test_source_change_during_copy_is_not_qualified(tmp_path, monkeypatch):
    original, output = tmp_path / "original.whl", tmp_path / "output.whl"
    original.write_bytes(b"pinned")
    copy = builder.shutil.copyfileobj
    def mutate(source, destination, length):
        copy(source, destination, length)
        original.write_bytes(b"modified after copy")
    monkeypatch.setattr(builder.shutil, "copyfileobj", mutate)
    with pytest.raises(ValueError, match="identity changed"):
        builder.copy_dependency(original, output, digest(original))


def test_run_reports_bounded_stderr_and_exit_without_raw_secrets_or_stdout(monkeypatch):
    key, secret = "sk-or-v1-FAKE_KEY_123456789", "FAKE_AWS_SECRET"
    stderr = ("x" * 10000 + "\nModuleNotFoundError: build_tool; " + key + " " + secret
              + " Authorization: Bearer HIDDEN_BEARER " + "https://user:pass@example.invalid/ https://OPAQUE_URL_TOKEN@example.invalid/")
    monkeypatch.setattr(builder.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 17, b"DO_NOT_LOG_STDOUT", stderr.encode()))
    with pytest.raises(ValueError) as failure:
        builder.run(["python", "DO_NOT_LOG_ARG"], cwd=".", env={"OPENROUTER_API_KEY": key, "AWS_SECRET_ACCESS_KEY": secret})
    text = str(failure.value)
    assert "exit 17" in text and "ModuleNotFoundError" in text and len(text) < 1800
    assert all(x not in text for x in [key, secret, "HIDDEN_BEARER", "user:pass", "OPAQUE_URL_TOKEN", "DO_NOT_LOG_STDOUT", "DO_NOT_LOG_ARG"])


def test_run_start_failure_does_not_echo_command(monkeypatch):
    def fail(*args, **kwargs):raise PermissionError(13, "DO_NOT_LOG_ERROR_VALUE", "DO_NOT_LOG_FILENAME")
    monkeypatch.setattr(builder.subprocess, "run", fail)
    with pytest.raises(ValueError, match="could not start") as failure:
        builder.run(["DO_NOT_LOG_COMMAND"], cwd=".", env={})
    assert "errno=13" in str(failure.value)
    assert "DO_NOT_LOG" not in str(failure.value)


def test_run_timeout_has_bounded_redacted_cause(monkeypatch):
    def fail(*args, **kwargs):raise subprocess.TimeoutExpired(["DO_NOT_LOG_COMMAND"], 300, stderr=b"waiting secret_value")
    monkeypatch.setattr(builder.subprocess, "run", fail)
    with pytest.raises(ValueError, match="timed out after 300s") as failure:
        builder.run(["DO_NOT_LOG_COMMAND"], cwd=".", env={"SERVICE_TOKEN": "secret_value"})
    assert "waiting" in str(failure.value) and "secret_value" not in str(failure.value)
    assert "DO_NOT_LOG_COMMAND" not in str(failure.value)
