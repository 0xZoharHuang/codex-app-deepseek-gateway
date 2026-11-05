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


def cds_boundary_fixture(tmp_path, version="0.6.9"):
    modules = {name: ("fixture-" + name).encode() for name in
               ["protocol.py", "formats.py", "structured.py", "protocol_errors.py", "search.py", "__init__.py"]}
    helper = b"fixture-reviewed-helper"
    dist_info = f"codex_deepseek_runtime-{version}.dist-info"
    license_path = dist_info + "/licenses/licenses/cpython-proactor-PSF.txt"
    license_bytes = b"fixture-complete-PSF-license"
    lock = {"dependencies": [{
                "distribution": "codex-deepseek-runtime",
                "filename": f"codex_deepseek_runtime-{version}-py3-none-any.whl",
                "version": version, "sha256": "",
                "source": {"source_commit": "fixed"}}],
            "protocol_boundary": {"source_commit": "fixed", "pure_files": {
                name: hashlib.sha256(value).hexdigest() for name, value in modules.items()}},
            "transport_compatibility": {"source_commit": "fixed", "files": {
                "windows_asyncio.py": hashlib.sha256(helper).hexdigest()}, "license_files": {
                license_path: hashlib.sha256(license_bytes).hexdigest()}}}
    members = {"codex_deepseek/" + name: value for name, value in modules.items()}
    members["codex_deepseek/windows_asyncio.py"] = helper
    members[license_path] = license_bytes
    members[dist_info + "/METADATA"] = (
        "Metadata-Version: 2.4\nName: codex-deepseek-runtime\n"
        f"Version: {version}\n\nOffline boundary fixture.\n").encode()
    return lock, members


def write_cds_fixture(tmp_path, lock, members):
    import zipfile
    wheel = tmp_path / lock["dependencies"][0]["filename"]
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    lock["dependencies"][0]["sha256"] = digest(wheel)
    return wheel


@pytest.mark.parametrize("version", ["0.6.9", "0.6.10"])
def test_protocol_and_single_transport_helper_and_license_are_all_verified(tmp_path, version):
    lock, members = cds_boundary_fixture(tmp_path, version)
    builder.verify_cds_dependency(write_cds_fixture(tmp_path, lock, members), lock)


@pytest.mark.parametrize("version", ["0.6.9", "0.6.10"])
@pytest.mark.parametrize("change", ["protocol", "helper", "license", "missing_helper", "missing_license",
                                  "empty_license_boundary", "extra_helper", "source_commit"])
def test_transport_boundary_rejects_tampering_or_missing_licensing(tmp_path, change, version):
    lock, members = cds_boundary_fixture(tmp_path, version)
    helper = "codex_deepseek/windows_asyncio.py"
    license_path = next(iter(lock["transport_compatibility"]["license_files"]))
    if change == "protocol": members["codex_deepseek/protocol.py"] = b"changed"
    elif change == "helper": members[helper] = b"changed"
    elif change == "license": members[license_path] = b"incomplete"
    elif change == "missing_helper": del members[helper]
    elif change == "missing_license": del members[license_path]
    elif change == "empty_license_boundary": lock["transport_compatibility"]["license_files"] = {}
    elif change == "extra_helper": lock["transport_compatibility"]["files"]["service.py"] = "0" * 64
    else: lock["transport_compatibility"]["source_commit"] = "another"
    wheel = write_cds_fixture(tmp_path, lock, members)
    expected = {
        "protocol": (ValueError, "Pinned pure CDS protocol differs"),
        "helper": (ValueError, "Pinned CDS transport helper differs"),
        "license": (ValueError, "Pinned CDS transport helper license differs"),
        "missing_helper": (KeyError, "windows_asyncio.py"),
        "missing_license": (KeyError, "cpython-proactor-PSF.txt"),
        "empty_license_boundary": (ValueError, "Unexpected CDS transport license boundary"),
        "extra_helper": (ValueError, "Unexpected CDS transport helper boundary"),
        "source_commit": (ValueError, "Transport helper source differs"),
    }
    exception, message = expected[change]
    with pytest.raises(exception, match=message):
        builder.verify_cds_dependency(wheel, lock)


@pytest.mark.parametrize("change", ["lock_version", "metadata_version", "metadata_name",
                                  "metadata_directory", "missing_metadata", "extra_metadata",
                                  "lock_sha", "lock_filename", "duplicate_dependency",
                                  "missing_dependency", "dependency_source_commit", "license_version"])
def test_cds_wheel_identity_and_versioned_license_stay_bound_to_lock(tmp_path, change):
    import copy
    lock, members = cds_boundary_fixture(tmp_path, "0.6.10")
    metadata_path = "codex_deepseek_runtime-0.6.10.dist-info/METADATA"
    if change == "metadata_version":
        members[metadata_path] = members[metadata_path].replace(b"Version: 0.6.10", b"Version: 0.6.9")
    elif change == "metadata_name":
        members[metadata_path] = members[metadata_path].replace(b"codex-deepseek-runtime", b"unrelated-package")
    elif change == "metadata_directory":
        members["codex_deepseek_runtime-0.6.9.dist-info/METADATA"] = members.pop(metadata_path)
    elif change == "missing_metadata": del members[metadata_path]
    elif change == "extra_metadata":
        members["unrelated-1.0.dist-info/METADATA"] = b"Name: unrelated\nVersion: 1.0\n"
    wheel = write_cds_fixture(tmp_path, lock, members)
    if change == "lock_version": lock["dependencies"][0]["version"] = "0.6.9"
    elif change == "lock_sha": lock["dependencies"][0]["sha256"] = "0" * 64
    elif change == "lock_filename": lock["dependencies"][0]["filename"] = "codex_deepseek_runtime-0.6.9-py3-none-any.whl"
    elif change == "duplicate_dependency": lock["dependencies"].append(copy.deepcopy(lock["dependencies"][0]))
    elif change == "missing_dependency": lock["dependencies"] = []
    elif change == "dependency_source_commit": lock["dependencies"][0]["source"]["source_commit"] = "another"
    elif change == "license_version":
        pin = lock["transport_compatibility"]["license_files"].pop(
            "codex_deepseek_runtime-0.6.10.dist-info/licenses/licenses/cpython-proactor-PSF.txt")
        lock["transport_compatibility"]["license_files"][
            "codex_deepseek_runtime-0.6.9.dist-info/licenses/licenses/cpython-proactor-PSF.txt"] = pin
    expected = {
        "lock_version": "metadata version differs",
        "metadata_version": "metadata version differs",
        "metadata_name": "metadata distribution differs",
        "metadata_directory": "metadata path differs",
        "missing_metadata": "one distribution metadata",
        "extra_metadata": "one distribution metadata",
        "lock_sha": "wheel SHA256 differs",
        "lock_filename": "wheel filename differs",
        "duplicate_dependency": "Exactly one pinned CDS dependency",
        "missing_dependency": "Exactly one pinned CDS dependency",
        "dependency_source_commit": "CDS wheel source differs",
        "license_version": "Unexpected CDS transport license boundary",
    }
    with pytest.raises(ValueError, match=expected[change]):
        builder.verify_cds_dependency(wheel, lock)
