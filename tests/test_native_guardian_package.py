"""P6 recipe/plumbing checks only: fake outputs/metadata, no Cargo or runtime."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("guardian_package_boundary", ROOT / "scripts/package_native_release.py")
pack = importlib.util.module_from_spec(spec); spec.loader.exec_module(pack)
spec = importlib.util.spec_from_file_location("guardian_build_boundary", ROOT / "scripts/build_native.py")
build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)

P5 = [
    "windows-home-namespace-0165.patch", "mcp-keyring-isolation-0165.patch",
    "guardian-thread-context-0165.patch", "windows-cwd-ancestor-metadata-0165.patch",
    "packaged-tectonic-materialization-0165.patch",
]
P6 = P5 + ["guardian-provider-transport-0165.patch"]
P6_SHA = "2d7504fcbfc86918c72eef7629760d590914b78c7bf705042e05f62749bacefe"
P5_SOURCE = "3830ef7a9318ff1c6016ecaa6840805250c527c9a2b75b1e77b6b9c334210548"
P6_SOURCE = "5d10fd97306cd2d11a1bc5d4ae2327468240bb650639cc93c39d19b2b7b58edb"


def recipe(patches=6):
    value = pack.load(ROOT / "native/build-lock.json")
    assert [x["file"] for x in value["patches"]] in (P5, P6, P6 + ["migration-checksum-eol-0165.patch"])
    current_source = value["prepared_source_sha256"]
    seventh = value["patches"].pop() if len(value["patches"]) == 7 else None
    if len(value["patches"]) == 6:
        assert value["patches"][-1] == {"file": P6[-1], "sha256": P6_SHA}
        value["patches"].pop()
    value["prepared_source_sha256"] = P5_SOURCE
    value["prepared_source_files"] = 9516
    if patches >= 6:
        value["patches"].append({"file": P6[-1], "sha256": P6_SHA})
        value["prepared_source_sha256"] = P6_SOURCE
    if patches == 7:
        assert seventh is not None
        value["patches"].append(seventh)
        value["prepared_source_sha256"] = current_source
    return value


def write_lock(path, lock):
    path.write_bytes((json.dumps(lock, indent=2) + "\n").encode())
    return path


def copied_native(repo):
    native = repo / "native"; native.mkdir(parents=True)
    for name in P6 + ["migration-checksum-eol-0165.patch"]:
        shutil.copyfile(ROOT / "native" / name, native / name)
    write_lock(native / "build-lock.json", recipe(5))
    return native


def stage_fixture(tmp_path, lock, lock_path):
    stage = tmp_path / "stage"; stage.mkdir()
    rows = []
    for name in sorted(pack.BINARIES):
        payload = ("offline fixture output " + name).encode()
        (stage / name).write_bytes(payload)
        rows.append({"name": name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    manifest = {"schema_version": 1, "status": "built-not-app-qualified",
                "candidate_revision": "O165", "build_lock_sha256": pack.sha(lock_path),
                "outputs": rows}
    pack.write(stage / "manifest.json", manifest)
    for name in ["LICENSE", "NOTICE", "licenses/codex-LICENSE", "licenses/codex-NOTICE"]:
        p = stage / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"offline license fixture")
    pack.write(stage / "licenses/rust/index.json", {"packages": []})
    pack.write(stage / "provenance/producer-references.json", {"fixture": True})
    shutil.copyfile(lock_path, stage / "provenance/O165-original-build-lock.json")
    for row in lock["patches"]:
        p = stage / "provenance/patches" / row["file"]; p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "native" / row["file"], p)
    return stage


def test_real_reviewed_six_patch_recipe_and_checksum_are_admitted():
    lock = recipe()
    assert lock["prepared_source_sha256"] == P6_SOURCE and lock["prepared_source_files"] == 9516
    assert pack.sha(ROOT / "native" / P6[-1]) == P6_SHA
    assert pack.single_graph(lock, ROOT / "native")["producer_id"] == "O165"
    command, = build.build_commands(Path("fixed-rust"), lock["binaries"])
    assert command.count("-p") == 2 and command.count("--bin") == 4 and "--bins" not in command


@pytest.mark.parametrize("bad", ["missing-tectonic", "reordered", "duplicate", "unknown", "checksum", "patch-bytes"])
def test_six_patch_admission_does_not_admit_unknown_omitted_or_changed_inputs(tmp_path, bad):
    native = copied_native(tmp_path / "repo"); lock = recipe()
    if bad == "missing-tectonic":
        lock["patches"].pop(4)
    elif bad == "reordered":
        lock["patches"][-2:] = reversed(lock["patches"][-2:])
    elif bad == "duplicate":
        lock["patches"].append(copy.deepcopy(lock["patches"][-1]))
    elif bad == "unknown":
        lock["patches"][-1]["file"] = "unreviewed-provider.patch"
    elif bad == "checksum":
        lock["patches"][-1]["sha256"] = "0" * 64
    else:
        (native / P6[-1]).write_bytes(b"unreviewed changed transport")
    with pytest.raises(ValueError):
        pack.single_graph(lock, native)


def test_explicit_six_patch_dry_run_preserves_active_lock_and_creates_no_build_state(tmp_path, monkeypatch, capsys):
    active = (ROOT / "native/build-lock.json").read_bytes()
    data = b"dry-run archive identity fixture"; archive = tmp_path / "upstream.tar.gz"; archive.write_bytes(data)
    lock = recipe(); lock["archive_sha256"] = hashlib.sha256(data).hexdigest()
    path = write_lock(tmp_path / "private-six.json", lock)
    cache = tmp_path / "cache"; (cache / "cargo").mkdir(parents=True)
    target = tmp_path / "target"; target.mkdir()
    work = tmp_path / "not-created-work"; output = tmp_path / "not-created-output"
    def versions(argv):
        assert argv[1:] in (["--version", "--verbose"], ["--version"])
        return ("rustc 1.99.1 (fixture)\nhost: x86_64-pc-windows-msvc"
                if argv[0].endswith("rustc.exe") else "cargo 1.99.1 (fixture)")
    monkeypatch.setattr(build, "checked_output", versions)
    monkeypatch.setattr(build.shutil, "which", lambda name: str(tmp_path / name))
    monkeypatch.setenv("LIB", "fixture SDK")
    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run must not launch compiler, source preparation or SDK probe")
    monkeypatch.setattr(build.subprocess, "run", forbidden)
    assert build.main(["--archive", str(archive), "--work-dir", str(work),
        "--output", str(output), "--cache-dir", str(cache), "--target-dir", str(target),
        "--toolchain-bin", str(tmp_path / "rust"), "--build-lock", str(path), "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["build_lock_sha256"] == build.sha(path)
    assert [r["file"] for r in plan["patches"]] == P6
    assert plan["source_sha256"] == P6_SOURCE
    assert len(plan["commands"]) == 1 and plan["commands"][0].count("--bin") == 4
    assert plan["cargo_home"] == str(cache / "cargo") and plan["target"] == str(target)
    assert not work.exists() and not output.exists() and not list(target.iterdir())
    assert (ROOT / "native/build-lock.json").read_bytes() == active


def test_private_six_patch_staging_and_archive_use_selected_lock_not_active_five(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; native = copied_native(repo)
    active = (native / "build-lock.json").read_bytes()
    lock = recipe(); path = write_lock(tmp_path / "private-six.json", lock)
    stage = stage_fixture(tmp_path, lock, path)
    monkeypatch.setattr(pack, "REPO", repo)
    with pytest.raises(ValueError, match="Unexpected public native file"):
        pack.validate_tree(stage)
    inventory = pack.validate_tree(stage, build_lock=path)
    assert {n for n in inventory if n.startswith("provenance/patches/")} == {"provenance/patches/" + n for n in P6}
    a = pack.make_archive(stage, tmp_path / "a.tar.gz", 1793418026, build_lock=path)
    b = pack.make_archive(stage, tmp_path / "b.tar.gz", 1793418026, build_lock=path)
    assert a == b and (native / "build-lock.json").read_bytes() == active
    assert "codex-code-mode-host.exe" not in inventory


@pytest.mark.parametrize("bad", ["lock-digest", "staged-patch-bytes", "missing-staged-patch",
                                  "staged-lock-bytes", "unknown-lock-topology"])
def test_explicit_staging_lock_does_not_weaken_receipt_or_patch_validation(tmp_path, monkeypatch, bad):
    repo = tmp_path / "repo"; copied_native(repo)
    lock = recipe(); path = write_lock(tmp_path / "private-six.json", lock)
    stage = stage_fixture(tmp_path, lock, path)
    monkeypatch.setattr(pack, "REPO", repo)
    if bad == "lock-digest":
        manifest = pack.load(stage / "manifest.json"); manifest["build_lock_sha256"] = "0" * 64
        (stage / "manifest.json").write_bytes(pack.canonical(manifest))
    elif bad == "staged-patch-bytes":
        (stage / "provenance/patches" / P6[-1]).write_bytes(b"altered staged public patch")
    elif bad == "missing-staged-patch":
        (stage / "provenance/patches" / P6[-1]).unlink()
    elif bad == "staged-lock-bytes":
        (stage / "provenance/O165-original-build-lock.json").write_bytes(b"altered original recipe")
    else:
        lock["patches"].append({"file": "unreviewed.patch", "sha256": "0" * 64}); write_lock(path, lock)
    with pytest.raises(ValueError):
        pack.validate_tree(stage, build_lock=path)


@pytest.mark.parametrize("patch_count", [5, 6, 7])
def test_packager_main_honors_private_lock_before_active_promotion_without_Cargo(tmp_path, monkeypatch, capsys, patch_count):
    # Tiny synthetic source/output receipts exercise wiring, not native qualification.
    repo = tmp_path / "repo"; native = copied_native(repo)
    (repo / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/build_native.py", repo / "scripts/build_native.py")
    for name in ["LICENSE", "NOTICE"]:
        shutil.copyfile(ROOT / name, repo / name)
    git = shutil.which("git.exe") or shutil.which("git")
    assert git, "existing Git is required; do not turn a missing prerequisite into a skip"
    subprocess.run([git, "init", "--quiet"], cwd=repo, check=True)
    subprocess.run([git, "-c", "user.name=Offline Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "--allow-empty", "--quiet", "-m", "offline packaging fixture"], cwd=repo, check=True)
    active = (native / "build-lock.json").read_bytes()
    source = tmp_path / "source"; (source / "codex-rs").mkdir(parents=True)
    (source / "codex-rs/Cargo.toml").write_bytes(b"[workspace]\nmembers=[]\n")
    (source / "codex-rs/Cargo.lock").write_bytes(b"offline source lock fixture\n")
    for name in ["LICENSE", "NOTICE"]:
        (source / name).write_bytes(b"offline upstream license fixture")
    state = build.source_state(source)
    lock = recipe(patch_count); lock["prepared_source_sha256"] = state["sha256"]
    lock["prepared_source_files"] = state["count"]; lock["cargo_lock_sha256"] = build.sha(source / "codex-rs/Cargo.lock")
    lock_path = write_lock(tmp_path / "private-lock.json", lock)
    raw = tmp_path / "raw"; raw.mkdir()
    binaries = []
    for name in lock["binaries"]:
        p = raw / name; p.write_bytes(("offline fake output " + name).encode())
        binaries.append({"file": name, "size": p.stat().st_size, "sha256": pack.sha(p)})
    cache = tmp_path / "cache"; (cache / "cargo").mkdir(parents=True)
    target = tmp_path / "separate-target"; target.mkdir()
    cargo = tmp_path / "rust/cargo.exe"
    value = {"schema_version": 1, "status": "built-not-app-qualified", "candidate_revision": "O165",
             "build_lock_sha256": pack.sha(lock_path), "upstream_commit": lock["upstream_commit"],
             "native_version": lock["native_version"], "archive_sha256": lock["archive_sha256"],
             "source_sha256": lock["prepared_source_sha256"], "patches": copy.deepcopy(lock["patches"]),
             "profile": copy.deepcopy(build.PROFILE), "commands": build.build_commands(cargo.parent, lock["binaries"]),
             "work_dir": str(tmp_path / "work"), "cache_dir": str(cache), "target": str(target),
             "cargo_home": str(cache / "cargo"), "source": str(source), "output": str(raw), "binaries": binaries}
    manifest = raw / "manifest.json"; pack.write(manifest, value)
    output = tmp_path / "public"; evidence = tmp_path / "evidence"
    real_run = subprocess.run; metadata_calls = []
    def offline_metadata(argv, *args, **kwargs):
        if argv[0] == str(cargo):
            assert argv[1:] == ["metadata", "--offline", "--locked", "--format-version", "1",
                              "--filter-platform", pack.TARGET, "--manifest-path", str(source / "codex-rs/Cargo.toml")]
            metadata_calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps({"packages": [], "resolve": {"nodes": []}}).encode(), b"")
        return real_run(argv, *args, **kwargs)
    monkeypatch.setattr(pack, "REPO", repo)
    monkeypatch.setattr(pack.subprocess, "run", offline_metadata)
    monkeypatch.setattr(sys, "argv", ["package_native_release.py", "--source-root", str(source),
        "--native-manifest", str(manifest), "--native-manifest-sha256", pack.sha(manifest),
        "--build-lock", str(lock_path), "--cargo", str(cargo), "--cargo-home", str(cache / "cargo"),
        "--git", git, "--output", str(output), "--evidence-dir", str(evidence), "--development-qualification"])
    assert pack.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["no_compile_or_native_execution"] is True and len(metadata_calls) == 1
    assert (output / "provenance/O165-original-build-lock.json").read_bytes() == lock_path.read_bytes()
    assert pack.load(output / "manifest.json")["build_lock_sha256"] == pack.sha(lock_path)
    assert {p.name for p in (output / "provenance/patches").iterdir()} == {row["file"] for row in lock["patches"]}
    assert (native / "build-lock.json").read_bytes() == active
    assert build.source_state(source) == state and not list(target.iterdir())
