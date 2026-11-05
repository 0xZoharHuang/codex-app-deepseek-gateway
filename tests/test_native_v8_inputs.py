from pathlib import Path
import hashlib
import importlib.util
import pytest

spec = importlib.util.spec_from_file_location("v8_build_inputs", Path(__file__).parents[1] / "scripts/build_native.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
TARGET = "x86_64-pc-windows-msvc"

def fixture(tmp_path, *, extra=False):
    source = tmp_path / "source"
    (source / "codex-rs").mkdir(parents=True)
    (source / "codex-rs/Cargo.lock").write_text('[[package]]\nname = "v8"\nversion = "152.2.0"\n', encoding="utf8")
    pins = source / "third_party/v8"
    pins.mkdir(parents=True)
    directory = tmp_path / "inputs"
    directory.mkdir()
    names = [f"rusty_v8_ptrcomp_sandbox_release_{TARGET}.lib.gz", f"src_binding_ptrcomp_sandbox_release_{TARGET}.rs"]
    for i, name in enumerate(names):
        (directory / name).write_bytes(f"artifact-{i}".encode())
    manifest = ("\r\n".join(f"{build.sha(directory / name)}  {name}" for name in names) + "\r\n").encode()
    if extra:
        manifest += ("0" * 64 + "  unexpected.rs\r\n").encode()
    mname = f"rusty_v8_ptrcomp_sandbox_release_{TARGET}.sha256"
    (directory / mname).write_bytes(manifest)
    (pins / "rusty_v8_152_2_0_release_manifests.sha256").write_text(hashlib.sha256(manifest).hexdigest() + "  " + mname + "\n", encoding="utf8")
    return source, directory, names, mname

def test_exact_official_sandbox_pair_and_original_crlf_manifest(tmp_path):
    source, directory, names, _ = fixture(tmp_path)
    result = build.verified_rusty_v8_inputs(source, directory, TARGET)
    assert result["environment"] == {"RUSTY_V8_ARCHIVE": str(directory / names[0]), "RUSTY_V8_SRC_BINDING_PATH": str(directory / names[1])}
    assert len(result["files"]) == 3 and result["profile"] == "ptrcomp_sandbox_release"

@pytest.mark.parametrize("which", ["manifest", "archive", "binding"])
def test_modified_inputs_fail_before_compiler(tmp_path, which):
    source, directory, names, mname = fixture(tmp_path)
    path = directory / ({"manifest": mname, "archive": names[0], "binding": names[1]}[which])
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        build.verified_rusty_v8_inputs(source, directory, TARGET)

def test_manifest_cannot_select_extra_or_standard_non_sandbox_artifacts(tmp_path):
    source, directory, _, _ = fixture(tmp_path, extra=True)
    with pytest.raises(ValueError, match="exactly the sandbox"):
        build.verified_rusty_v8_inputs(source, directory, TARGET)

def test_inherited_v8_override_cannot_bypass_source_pins(tmp_path, monkeypatch):
    monkeypatch.setenv("RUSTY_V8_ARCHIVE", "unverified")
    monkeypatch.setenv("V8_FROM_SOURCE", "1")
    env = build.build_environment(tmp_path, tmp_path)
    assert "RUSTY_V8_ARCHIVE" not in env and "V8_FROM_SOURCE" not in env
