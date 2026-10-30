"""Fail fast on real toolchain capability; no generated probe is executed."""
from pathlib import Path
import importlib.util
import json
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location("native_sdk_preflight", Path(__file__).parents[1] / "scripts/build_native.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def fake_compiler(monkeypatch, *, status=0, artifacts=True):
    calls = []
    monkeypatch.setattr(build.shutil, "which", lambda name, **kwargs: "cl.exe")
    def run(args, **kwargs):
        calls.append((args, kwargs))
        assert args[0] == "cl.exe" and "/MT" in args and "/link" in args
        source = Path(args[args.index("/TC") + 1])
        assert "#include <stddef.h>" in source.read_text()
        assert "#include <windows.h>" in source.read_text()
        assert "_M_X64" in source.read_text()
        if artifacts:
            for option in ("/Fo", "/Fe"):
                Path(next(arg[len(option):] for arg in args if arg.startswith(option))).write_bytes(b"fixture")
        return subprocess.CompletedProcess(args, status, "", "missing SDK" if status else "")
    monkeypatch.setattr(build.subprocess, "run", run)
    return calls


def test_compile_link_uses_same_child_environment_and_never_executes_probe(monkeypatch):
    calls = fake_compiler(monkeypatch)
    environment = {"PATH": "fixture", "INCLUDE": "SDK", "LIB": "SDK", "CL": "/DFIXTURE"}
    before = dict(environment)
    result = build.preflight_windows_msvc(environment, "x86_64-pc-windows-msvc")
    assert result["status"] == "compile-link-passed" and result["probe_program_executed"] is False
    assert len(calls) == 1 and calls[0][1]["env"] == before and environment == before
    assert not Path(calls[0][1]["cwd"]).exists()


def test_compiler_failure_is_not_mistaken_for_existing_LIB_string(monkeypatch):
    calls = fake_compiler(monkeypatch, status=2, artifacts=False)
    with pytest.raises(ValueError, match="compile-link preflight failed"):
        build.preflight_windows_msvc({"PATH": "fixture", "LIB": "exists"}, "x86_64-pc-windows-msvc")
    assert len(calls) == 1 and not Path(calls[0][1]["cwd"]).exists()


def test_return_zero_without_linked_artifact_still_fails(monkeypatch):
    fake_compiler(monkeypatch, artifacts=False)
    with pytest.raises(ValueError, match="complete object and executable"):
        build.preflight_windows_msvc({"PATH": "fixture"}, "x86_64-pc-windows-msvc")


def test_missing_compiler_refused_without_starting_anything(monkeypatch):
    monkeypatch.setattr(build.shutil, "which", lambda *args, **kwargs: None)
    monkeypatch.setattr(build.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="cl.exe"):
        build.preflight_windows_msvc({}, "x86_64-pc-windows-msvc")


def test_timeout_is_explicit_and_does_not_mutate_caller_environment(monkeypatch):
    monkeypatch.setattr(build.shutil, "which", lambda *args, **kwargs: "cl.exe")
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 30)
    monkeypatch.setattr(build.subprocess, "run", timeout)
    environment = {"PATH": "fixture"}
    with pytest.raises(ValueError, match="TimeoutExpired"):
        build.preflight_windows_msvc(environment, "x86_64-pc-windows-msvc")
    assert environment == {"PATH": "fixture"}


def test_unknown_target_is_not_silently_treated_as_x64(monkeypatch):
    monkeypatch.setattr(build.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="unsupported"):
        build.preflight_windows_msvc({}, "unknown-target")


@pytest.mark.skipif(build.os.name != "nt", reason="Windows native build entrypoint")
def test_bad_sdk_stops_before_build_ownership_source_or_output_mutation(tmp_path, monkeypatch):
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"source fixture")
    work, cache, toolchain = (tmp_path / name for name in ("new-work", "cache", "rust"))
    cache.mkdir()
    lock = {
        "schema_version": 1, "release_environment": build.PROFILE,
        "archive_sha256": build.sha(archive), "patches": [], "binaries": ["codex.exe"],
        "rust_version": "1.99.1", "target": "x86_64-pc-windows-msvc",
        "upstream_commit": "fixed", "native_version": "fixture",
        "prepared_source_sha256": "1" * 64,
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    monkeypatch.setattr(build, "checked_output", lambda args:
        "rustc 1.99.1 (fixture)\nhost: x86_64-pc-windows-msvc"
        if args[0].endswith("rustc.exe") else "cargo 1.99.1 (fixture)")
    monkeypatch.setattr(build.shutil, "which", lambda name, **kwargs: str(tmp_path / name))
    monkeypatch.setenv("LIB", "string-only")
    def sdk_failed(*args):
        raise ValueError("real SDK probe failed")
    monkeypatch.setattr(build, "preflight_windows_msvc", sdk_failed)
    monkeypatch.setattr(build, "prepare_source", lambda *args: pytest.fail("source mutation happened"))
    with pytest.raises(ValueError, match="real SDK probe failed"):
        build.main(["--archive", str(archive), "--work-dir", str(work),
                    "--cache-dir", str(cache), "--toolchain-bin", str(toolchain),
                    "--build-lock", str(lock_path)])
    assert not work.exists() and not list(cache.iterdir())


def test_explicit_target_is_separate_from_Cargo_home_and_does_not_change_global_env(tmp_path, monkeypatch):
    cache, target, source, output = (tmp_path / n for n in ("cache", "target", "source", "output"))
    cache.mkdir(); target.mkdir()
    selected = build.target_directory(cache, source, output, target)
    env = build.build_environment(tmp_path, tmp_path / "rust", cache, target_dir=selected)
    assert env["CARGO_HOME"] == str(cache / "cargo")
    assert env["CARGO_TARGET_DIR"] == str(target)
    assert build.target_directory(cache, source, output, None) == cache / "target"


@pytest.mark.parametrize("kind", ["source", "output", "cargo", "source-parent", "file"])
def test_target_path_conflicts_fail_before_any_Cargo_operation(tmp_path, kind):
    cache, source, output = (tmp_path / n for n in ("cache", "source", "output"))
    cache.mkdir(); source.mkdir(); output.mkdir(); (cache / "cargo").mkdir()
    choices = {"source": source, "output": output, "cargo": cache / "cargo",
               "source-parent": tmp_path, "file": tmp_path / "file"}
    (tmp_path / "file").write_bytes(b"not a directory")
    with pytest.raises(ValueError):
        build.target_directory(cache, source, output, choices[kind])


def test_only_actual_cache_entries_are_admitted_for_an_external_target(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()
    (cache / "target").write_bytes(b"unused old location")
    with pytest.raises(ValueError, match="cache entries"):
        build.cache_directory(tmp_path / "work", tmp_path / "source", tmp_path / "output", cache)
    assert build.cache_directory(tmp_path / "work", tmp_path / "source", tmp_path / "output",
                                 cache, external_target=True) == cache
