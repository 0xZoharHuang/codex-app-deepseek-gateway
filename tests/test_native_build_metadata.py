# Pure build-metadata checks; no compiler, runtime or setup process is run.
from pathlib import Path
import hashlib
import importlib.util
import io
import json
import tarfile

import pytest

SPEC = importlib.util.spec_from_file_location("native_build_metadata", Path(__file__).parents[1] / "scripts/build_native.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def record(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def archive(tmp_path, entries):
    target = tmp_path / "source.tar.gz"
    with tarfile.open(target, "w:gz") as stream:
        for name, data in entries:
            member = tarfile.TarInfo("codex-fixed/" + name)
            if isinstance(data, bytes):
                member.size = len(data)
                stream.addfile(member, io.BytesIO(data))
            else:
                member.type = tarfile.SYMTYPE
                member.linkname = data
                stream.addfile(member)
    return target


def test_inventory_reports_all_change_kinds_and_exact_preimages(tmp_path):
    original = archive(tmp_path, [("a.rs", b"old"), ("delete.rs", b"gone"), ("keep.rs", b"same")])
    prepared = {"a.rs": record(b"new"), "new.rs": record(b"added"), "keep.rs": record(b"same")}
    result = build.source_changes(original, "fixed", prepared)
    assert result == [
        {"path": "a.rs", "kind": "modified", "before": record(b"old"), "after": record(b"new")},
        {"path": "delete.rs", "kind": "deleted", "before": record(b"gone"), "after": None},
        {"path": "new.rs", "kind": "added", "before": None, "after": record(b"added")},
    ]


def test_help_change_cannot_be_omitted_by_old_summary(tmp_path):
    path = "codex-rs/cli/src/sandbox_uninstall.rs"
    original = archive(tmp_path, [(path, b"old-help"), ("unchanged", b"same")])
    prepared = {path: record(b"new-help"), "unchanged": record(b"same")}
    assert [row["path"] for row in build.source_changes(original, "fixed", prepared)] == [path]


def test_materialized_archive_license_link_is_not_a_source_change(tmp_path):
    original = archive(tmp_path, [("COPYING", b"license"), ("LICENSE", "COPYING")])
    assert build.source_changes(original, "fixed", {name: record(b"license") for name in ("COPYING", "LICENSE")}) == []


def test_duplicate_archive_entry_fails_closed(tmp_path):
    original = archive(tmp_path, [("same", b"first"), ("same", b"second")])
    with pytest.raises(ValueError, match="duplicate"):
        build.source_changes(original, "fixed", {"same": record(b"second")})


def test_foreground_candidate_preserves_exact_J_producer_and_selects_only_M_CLI():
    native = Path(__file__).parents[1] / "native"
    raw = (native / "build-lock.M.json").read_bytes()
    lock = json.loads(raw)
    assert raw == (json.dumps(lock, indent=2) + "\n").encode()
    assert lock["candidate_revision"] == "M" and lock["binaries"] == ["codex.exe"]
    assert lock["prepared_source_sha256"] == "1a365be666950457805ca434eb3f85cedb162e542b89ed3d449b82099dd0d4f2"
    assert lock["prepared_source_files"] == 9201 and lock["release_environment"] == build.PROFILE
    assert len(lock["patches"]) == 18
    assert lock["patches"][-1] == {
        "file": "windows-setup-refresh-handles.patch",
        "sha256": "7b65691a04c1fb1309f94cab6621f423b711b0256f85c94326d82da1fc15fe76",
    }
    for patch in lock["patches"]:
        assert build.sha(native / patch["file"]) == patch["sha256"]
    jraw = (native / "build-lock.J.json").read_bytes()
    assert hashlib.sha256(jraw).hexdigest() == "f878eaaad7c9d8c3f1c9211ac5e221b07fb47c11c22a2e5e1b9f68311cb5ad85"
    previous = json.loads(jraw)
    assert lock["patches"][:-1] == previous["patches"]
    for key in ("upstream_commit", "archive_sha256", "native_version", "rust_version",
                "target", "cargo_lock_sha256", "release_environment"):
        assert lock[key] == previous[key]


def test_locked_M_build_does_not_recompile_the_qualified_J_siblings():
    command, = build.build_commands(Path("pinned-rust"), ["codex.exe"])
    assert command == [
        str(Path("pinned-rust") / "cargo.exe"), "build", "--locked", "--release",
        "-p", "codex-cli", "--bin", "codex",
    ]
    j = json.loads((Path(__file__).parents[1] / "native/build-lock.J.json").read_bytes())
    original, = build.build_commands(Path("pinned-rust"), j["binaries"])
    assert original.count("--bin") == 4 and original.count("-p") == 2
    assert "--bins" not in original


@pytest.mark.parametrize("targets", [[], ["unknown.exe"], ["codex.exe", "codex.exe"], "codex.exe", [None]])
def test_build_targets_reject_missing_unknown_duplicate_or_malformed_entries(targets):
    with pytest.raises(ValueError, match="Unsupported or duplicate"):
        build.build_commands(Path("pinned-rust"), targets)


def test_foreground_patch_changes_only_native_launch_boundary_and_its_controls():
    patch = (Path(__file__).parents[1] / "native/windows-setup-refresh-handles.patch").read_text(encoding="utf-8")
    changed = {line[6:].split("\t")[0] for line in patch.splitlines() if line.startswith("+++ b/")}
    assert changed == {
        "codex-rs/windows-sandbox-rs/src/setup.rs",
        "codex-rs/windows-sandbox-rs/src/setup_refresh_launch.rs",
        "codex-rs/windows-sandbox-rs/src/setup_refresh_launch_tests.rs",
    }
    # Exact patch bytes are bound above; Win32 behavior is tested by the
    # compiled native component and real lifecycle, not guessed source keywords.


def test_replay_candidate_preserves_M_and_J_provenance_and_selects_only_N_CLI():
    native = Path(__file__).parents[1] / "native"
    mraw = (native / "build-lock.M.json").read_bytes()
    assert hashlib.sha256(mraw).hexdigest() == "3259ede15d27dcc590a62f6bdeea1963357c1f3b2133d21664688cfc420ea1d9"
    previous = json.loads(mraw)
    raw = (native / "build-lock.json").read_bytes()
    lock = json.loads(raw)
    assert raw == (json.dumps(lock, indent=2) + "\n").encode()
    assert lock["candidate_revision"] == "N" and lock["binaries"] == ["codex.exe"]
    assert lock["prepared_source_sha256"] == "3be6efb7b73138f54cae24f6f6f04da5a348b02483f2a36dbeddc766689a77bc"
    assert lock["prepared_source_files"] == 9201
    assert len(lock["patches"]) == 19 and lock["patches"][:-1] == previous["patches"]
    assert lock["patches"][-1] == {
        "file": "payload-request-capacity-replay.patch",
        "sha256": "bce3a17c66fafbdeaa6112afbbbdd28c2fd70852fb0c436cb5d7ccc767b58fee",
    }
    for patch in lock["patches"]:
        assert build.sha(native / patch["file"]) == patch["sha256"]
    for key in ("upstream_commit", "archive_sha256", "native_version", "rust_version",
                "target", "cargo_lock_sha256", "release_environment", "binaries"):
        assert lock[key] == previous[key]
    command, = build.build_commands(Path("pinned-rust"), lock["binaries"])
    assert command == [
        str(Path("pinned-rust") / "cargo.exe"), "build", "--locked", "--release",
        "-p", "codex-cli", "--bin", "codex",
    ]
    assert "--bins" not in command and "codex-windows-sandbox" not in command


def test_replay_overlay_changes_only_checkpoint_hash_and_native_regressions():
    patch = (Path(__file__).parents[1] / "native/payload-request-capacity-replay.patch").read_bytes()
    assert hashlib.sha256(patch).hexdigest() == "bce3a17c66fafbdeaa6112afbbbdd28c2fd70852fb0c436cb5d7ccc767b58fee"
    changed = {line[6:].split("\t")[0] for line in patch.decode("utf-8").splitlines()
               if line.startswith("+++ b/")}
    assert changed == {"codex-rs/core/src/session/request_capacity.rs"}
    # Exact reviewed patch binds the three native serde/legacy regression tests.
    # Their Rust and App execution receipts remain separate acceptance evidence.
