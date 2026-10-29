"""Actual user-config publication I/O, never a repeated apply/restore operation."""
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import pytest
from codex_app_deepseek import app_config as config
from test_app_config import paths, prepared
from test_isolated_app import _windows_security_bytes

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def busy(code):
    error = PermissionError(13, "controlled replacement failure")
    if code is not None:
        error.winerror = code
    return error

@pytest.mark.parametrize("explicit_absent", [False, True])
def test_publish_expected_hash_refuses_later_edit_or_created_target_before_temp(tmp_path, explicit_absent):
    target = tmp_path / "config.toml"
    before = b"old = true\n"
    if not explicit_absent:
        target.write_bytes(before)
    expected = None if explicit_absent else digest(before)
    later = b"later_user_edit = true\n"; target.write_bytes(later)
    with pytest.raises(config.ConfigurationConflict, match="before atomic publication"):
        config._atomic_write(target, b"replacement = true\n",
                             expected_sha256=expected, retry_transient=True)
    assert target.read_bytes() == later
    assert list(tmp_path.glob(".app-deepseek-*.tmp")) == []

@pytest.mark.parametrize("code", [None, 2, 87])
def test_config_publish_nonsharing_errors_are_not_retried(tmp_path, monkeypatch, code):
    target = tmp_path / "config.toml"; target.write_bytes(b"old\n")
    original = os.replace; attempts = []; sleeps = []; error = busy(code)
    def blocked(source, destination):
        if Path(destination) == target:
            attempts.append(source); raise error
        return original(source, destination)
    monkeypatch.setattr(config.os, "replace", blocked)
    monkeypatch.setattr(config.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError) as caught:
        config._atomic_write(target, b"new\n", expected_sha256=digest(b"old\n"),
                             retry_transient=True)
    assert caught.value is error and len(attempts) == 1 and sleeps == []
    assert target.read_bytes() == b"old\n"
    assert list(tmp_path.glob(".app-deepseek-*.tmp")) == []

@pytest.mark.skipif(os.name != "nt", reason="WinError replacement contract")
@pytest.mark.parametrize("code", [5, 32, 33])
def test_config_publish_bounded_same_temp_fsynced_once(tmp_path, monkeypatch, code):
    target = tmp_path / "config.toml"; target.write_bytes(b"old\n")
    original_replace, original_temp, original_fsync = os.replace, config.tempfile.mkstemp, os.fsync
    attempts, created, synced, sleeps = [], [], [], []
    error = busy(code)
    def blocked(source, destination):
        if Path(destination) == target:
            attempts.append(Path(source)); raise error
        return original_replace(source, destination)
    def temporary(*args, **kwargs):
        result = original_temp(*args, **kwargs); created.append(result[1]); return result
    def fsync(fd):
        synced.append(fd); return original_fsync(fd)
    monkeypatch.setattr(config.os, "replace", blocked)
    monkeypatch.setattr(config.tempfile, "mkstemp", temporary)
    monkeypatch.setattr(config.os, "fsync", fsync)
    monkeypatch.setattr(config.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError) as caught:
        config._atomic_write(target, b"new\n", expected_sha256=digest(b"old\n"),
                             retry_transient=True)
    assert caught.value is error
    assert len(attempts) == 8 and len(set(attempts)) == len(created) == len(synced) == 1
    assert len(sleeps) == 7 and max(sleeps) <= .2 and sum(sleeps) < 1
    assert target.read_bytes() == b"old\n"
    assert list(tmp_path.glob(".app-deepseek-*.tmp")) == []

@pytest.mark.skipif(os.name != "nt", reason="real Windows reader denies replacement")
@pytest.mark.parametrize("operation", ["apply", "restore", "metadata", "receipt", "lifecycle", "service"])
def test_real_short_reader_releases_one_public_operation(paths, monkeypatch, operation):
    config_path, _, _, backups = paths
    target = config_path if operation in {"apply", "restore"} else config_path.parent / (operation + ".json")
    original_bytes = b'# keep original\ncustom_key = "keep"\n'
    target.write_bytes(original_bytes)
    if operation == "restore":
        checkpoint = config.apply(prepared(paths), backup_dir=backups)
    before = target.read_bytes()
    held = target.open("rb")
    real_replace = os.replace
    blocked = threading.Event(); errors = []; attempts = []
    def replace(source, destination):
        if Path(destination) != target:
            return real_replace(source, destination)
        attempts.append(Path(source))
        try:
            return real_replace(source, destination)
        except PermissionError as error:
            assert getattr(error, "winerror", None) in {5, 32, 33}
            assert target.read_bytes() == before
            blocked.set()
            raise
    def release():
        try:
            assert blocked.wait(5), "controlled first collision never happened"
            time.sleep(.04)
        except BaseException as error:
            errors.append(error)
        finally:
            held.close()
    thread = threading.Thread(target=release); thread.start()
    monkeypatch.setattr(config.os, "replace", replace)
    try:
        if operation == "apply":
            checkpoint = config.apply(prepared(paths), backup_dir=backups)
            assert json.loads(checkpoint.manifest_path.read_text(encoding="utf-8"))["state"] == "applied"
            assert b'custom_key = "keep"' in target.read_bytes()
        elif operation == "restore":
            config.restore(checkpoint.manifest_path)
            assert target.read_bytes() == original_bytes
            assert json.loads(checkpoint.manifest_path.read_text(encoding="utf-8"))["state"] == "restored"
        else:
            from codex_app_deepseek import isolated_app as app, lifecycle, cli
            publisher = {"metadata": app.save, "receipt": app._save_receipt,
                         "lifecycle": lifecycle._write,
                         "service": lambda p, value: cli._write_service_record(p.parent, value)}[operation]
            publisher(target, {"generation": "new"})
            assert json.loads(target.read_bytes()) == {"generation": "new"}
    finally:
        blocked.set(); held.close(); thread.join(5)
    assert not thread.is_alive() and errors == []
    assert 2 <= len(attempts) <= 8 and len(set(attempts)) == 1
    assert list(target.parent.glob(".app-deepseek-*.tmp")) == []

@pytest.mark.skipif(os.name != "nt", reason="real Windows reader collision followed by independent edit")
@pytest.mark.parametrize("change", ["content", "same_content_new_identity", "delete"])
def test_publish_aborts_on_external_target_change_during_retry(tmp_path, monkeypatch, change):
    target = tmp_path / "config.toml"; before = b"old = true\n"; target.write_bytes(before)
    before_id = target.stat().st_ino
    replacement = tmp_path / "independent-editor.tmp"; replacement.write_bytes(before)
    replacement_id = replacement.stat().st_ino
    held = target.open("rb"); original = os.replace; attempts = []
    later = b"later_user_edit = true\n"
    def replace(source, destination):
        if Path(destination) != target:
            return original(source, destination)
        attempts.append(source)
        try:
            return original(source, destination)
        except PermissionError as error:
            assert getattr(error, "winerror", None) in {5, 32, 33}
            held.close()
            if change == "content":
                target.write_bytes(later)
            elif change == "same_content_new_identity":
                assert replacement_id != before_id
                original(replacement, target)
                assert target.read_bytes() == before and target.stat().st_ino == replacement_id
            else:
                target.unlink()
            raise
    monkeypatch.setattr(config.os, "replace", replace)
    try:
        with pytest.raises(config.ConfigurationConflict, match="target changed"):
            config._atomic_write(target, b"replacement = true\n",
                                 expected_sha256=digest(before), retry_transient=True)
    finally:
        held.close()
    assert len(attempts) == 1, "a later edit must prevent the second replace call"
    if change == "content":
        assert target.read_bytes() == later
    elif change == "same_content_new_identity":
        assert target.read_bytes() == before and target.stat().st_ino == replacement_id
    else:
        assert not target.exists()
    assert list(tmp_path.glob(".app-deepseek-*.tmp")) == []

def test_public_apply_restore_supply_known_expected_digest(paths, monkeypatch):
    target, _, _, backups = paths
    target.write_bytes(b'custom_key = "keep"\n')
    original_bytes = target.read_bytes(); actual_atomic = config._atomic_write; calls = []
    def capture(path, data, **kwargs):
        if path == target:
            calls.append(dict(kwargs))
        return actual_atomic(path, data, **kwargs)
    monkeypatch.setattr(config, "_atomic_write", capture)
    checkpoint = config.apply(prepared(paths), backup_dir=backups)
    applied = target.read_bytes()
    config.restore(checkpoint.manifest_path)
    assert target.read_bytes() == original_bytes
    assert calls == [{"expected_sha256": digest(original_bytes), "retry_transient": True},
                     {"expected_sha256": digest(applied), "retry_transient": True}]

@pytest.mark.skipif(os.name != "nt", reason="real Windows DACL deletion denial")
@pytest.mark.parametrize("publisher", ["config", "metadata", "receipt", "lifecycle", "service"])
def test_permanent_DACL_deny_is_not_weakened_or_bypassed(tmp_path, monkeypatch, publisher):
    import ctypes
    from ctypes import wintypes
    from codex_app_deepseek.isolated_app import WindowsBackend
    target = tmp_path / ("service.json" if publisher == "service" else "config.toml")
    target.write_bytes(b"old\n")
    sid = WindowsBackend().identity(os.getpid())["sid"]
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    adv.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    adv.SetFileSecurityW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]; kernel.LocalFree.restype = ctypes.c_void_p
    before = {p: _windows_security_bytes(p) for p in [tmp_path, target]}
    def set_descriptor(path, value):
        data = ctypes.create_string_buffer(value)
        control = int.from_bytes(value[2:4], "little")
        flags = 4 | (0x80000000 if control & 0x1000 else 0x20000000)
        assert adv.SetFileSecurityW(str(path), flags, data), ctypes.get_last_error()
    def deny(path, mask):
        descriptor = ctypes.c_void_p()
        sddl = f"D:P(D;;{mask};;;{sid})(A;;FA;;;{sid})(A;;FA;;;SY)"
        assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None)
        try:
            assert adv.SetFileSecurityW(str(path), 0x80000004, descriptor), ctypes.get_last_error()
        finally:
            kernel.LocalFree(descriptor)
    attempts = []; real_replace = os.replace
    def replace(source, destination):
        if Path(destination) == target:
            attempts.append(source)
        return real_replace(source, destination)
    try:
        deny(tmp_path, "0x40")  # FILE_DELETE_CHILD on this parent only.
        deny(target, "0x10000")  # DELETE on the target itself.
        denied = {p: _windows_security_bytes(p) for p in before}
        monkeypatch.setattr(config.os, "replace", replace)
        started = time.monotonic()
        with pytest.raises(PermissionError) as caught:
            if publisher == "config":
                config._atomic_write(target, b"new\n", expected_sha256=digest(b"old\n"),
                                     retry_transient=True)
            else:
                from codex_app_deepseek import isolated_app as app, lifecycle, cli
                write = {"metadata": app.save, "receipt": app._save_receipt,
                         "lifecycle": lifecycle._write,
                         "service": lambda p, value: cli._write_service_record(p.parent, value)}[publisher]
                write(target, {"new": True})
        elapsed = time.monotonic() - started
        assert getattr(caught.value, "winerror", None) in {5, 32, 33}
        assert len(attempts) == 8 and elapsed < 5
        assert target.read_bytes() == b"old\n"
        assert all(_windows_security_bytes(p) == value for p, value in denied.items())
    finally:
        for path in [target, tmp_path]:
            set_descriptor(path, before[path])
    assert list(tmp_path.glob(".app-deepseek-*.tmp")) == []

@pytest.mark.skipif(os.name != "nt", reason="actual Windows lstat/fstat legacy time fields")
def test_snapshot_binds_rewritten_file_without_comparing_inconsistent_legacy_ctime(tmp_path):
    target = tmp_path / "config.toml"; target.write_bytes(b"first\n")
    time.sleep(.01)
    target.write_bytes(b"second\n")
    with target.open("rb") as source:
        info = os.fstat(source.fileno())
    named = target.lstat()
    assert named.st_ino == info.st_ino and named.st_mtime_ns == info.st_mtime_ns
    # Current Python can differ on st_ctime; inode and current content cannot.
    state = config._atomic_target_state(target)
    assert state[1] == digest(b"second\n")
    config._atomic_write(target, b"third\n", expected_sha256=digest(b"second\n"),
                         retry_transient=True)
    assert target.read_bytes() == b"third\n"


def test_lifecycle_and_service_producers_keep_their_existing_JSON_wire_bytes(tmp_path):
    from codex_app_deepseek import lifecycle, cli
    value = {"label": "\u76ee\u5f55", "n": 1}
    path = tmp_path / "lifecycle.json"
    lifecycle._write(path, value)
    cli._write_service_record(tmp_path, value)
    assert path.read_bytes() == (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert (tmp_path / "service.json").read_bytes() == json.dumps(value, indent=2).encode("utf-8")


@pytest.mark.skipif(os.name != "nt", reason="real Windows legacy producer ACL inheritance")
def test_consolidated_metadata_publishers_preserve_legacy_inherited_and_private_ACLs(tmp_path):
    import ctypes
    from ctypes import wintypes
    import tempfile
    from codex_app_deepseek import lifecycle, cli
    from codex_app_deepseek.isolated_app import WindowsBackend
    parent = tmp_path / "metadata"; parent.mkdir()
    sid = WindowsBackend().identity(os.getpid())["sid"]
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    adv.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    adv.SetFileSecurityW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]; kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    sddl = f"D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)(A;OICI;FR;;;BU)"
    assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None)
    try:
        assert adv.SetFileSecurityW(str(parent), 0x80000004, descriptor)
    finally:
        kernel.LocalFree(descriptor)

    value = {"generation": "old", "label": "\u76ee\u5f55"}
    legacy = parent / "lifecycle.json"
    fd, temporary = tempfile.mkstemp(prefix=".lifecycle-", dir=parent)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, legacy)
    inherited = _windows_security_bytes(legacy)
    lifecycle._write(legacy, {"generation": "new"})
    assert _windows_security_bytes(legacy) == inherited

    service = parent / "service.json"
    fd, temporary = tempfile.mkstemp(prefix=".service-", suffix=".tmp", dir=parent)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        config._private(Path(temporary))
        json.dump(value, stream, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, service)
    private = _windows_security_bytes(service)
    assert private != inherited, "controlled parent ACL must distinguish the two original policies"
    cli._write_service_record(parent, {"generation": "new"})
    assert _windows_security_bytes(service) == private
