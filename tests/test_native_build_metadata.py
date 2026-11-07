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
    raw = (native / "build-lock.N.json").read_bytes()
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


def test_current_0165_recipe_is_a_single_four_target_native_producer():
    native = Path(__file__).parents[1] / "native"
    raw = (native / "build-lock.json").read_bytes()
    lock = json.loads(raw)
    assert lock["candidate_revision"] == "O165"
    assert lock["upstream_commit"] == "720fec862a608ae1e5878d1eecb04fb93192e93c"
    assert lock["native_version"] == "0.165.0-alpha.1.2"
    assert lock["target"] == "x86_64-pc-windows-msvc"
    assert set(lock["binaries"]) == set(build.NATIVE_TARGETS) and len(lock["binaries"]) == 4
    assert lock["release_environment"] == build.PROFILE
    five = [
        "windows-home-namespace-0165.patch", "mcp-keyring-isolation-0165.patch",
        "guardian-thread-context-0165.patch", "windows-cwd-ancestor-metadata-0165.patch",
        "packaged-tectonic-materialization-0165.patch"]
    six = five + ["guardian-provider-transport-0165.patch"]
    names = [row["file"] for row in lock["patches"]]
    seven = six + ["migration-checksum-eol-0165.patch"]
    nine = seven + ["cadsg-request-capacity-port-0165.patch",
                    "cadsg-request-capacity-cancel-0165.patch"]
    ten = nine + ["windows-host-boundaries-0165.patch"]
    assert names == ten
    expected_source = {
        5: "3830ef7a9318ff1c6016ecaa6840805250c527c9a2b75b1e77b6b9c334210548",
        6: "5d10fd97306cd2d11a1bc5d4ae2327468240bb650639cc93c39d19b2b7b58edb",
        7: "1125f5c87733fb267177522de600918b06a0a41489543b57c721a3bcce6d7c56",
        9: "3c17e4f4714cf20b8ba032cd6ea8f94d0a4743312c08cf7f6686198d5740dfb5",
        10: "495125857fc45dc71a780d16832f2b2132ad129aea72f0353ebd12adeafc0c9b",
    }
    assert lock["prepared_source_sha256"] == expected_source[len(names)]
    assert lock["prepared_source_files"] == 9518
    for row in lock["patches"]:
        assert build.sha(native / row["file"]) == row["sha256"]
    command, = build.build_commands(Path("pinned-rust"),lock["binaries"])
    assert command.count("-p") == 2 and command.count("--bin") == 4
    assert "--bins" not in command
    baseline = json.loads((native / "app-launch-baseline.json").read_bytes())
    assert baseline["native_version"] == lock["native_version"]
    assert baseline["native_upstream_commit"] == lock["upstream_commit"]


def test_cache_default_keeps_existing_work_layout_and_release_environment(tmp_path,monkeypatch):
    work = tmp_path / "new-build"; rust = tmp_path / "pinned-rust"
    monkeypatch.setenv("RUSTFLAGS","inherited-unsafe")
    monkeypatch.setenv("CARGO_HOME","inherited-wrong-cache")
    monkeypatch.setenv("CARGO_PROFILE_RELEASE_LTO","inherited-wrong-profile")
    assert build.cache_directory(work,work / "source",work / "candidate",None) == work
    env = build.build_environment(work,rust)
    assert env["CARGO_HOME"] == str(work / "cargo")
    assert env["CARGO_TARGET_DIR"] == str(work / "target")
    assert env["RUSTC"] == str(rust / "rustc.exe")
    assert "RUSTFLAGS" not in env
    assert all(env[key] == value for key,value in build.PROFILE.items())
    assert not work.exists(), "Selecting/default planning must not create build state"


def test_explicit_existing_cache_changes_only_cargo_and_target_paths(tmp_path):
    work = tmp_path / "new-build"; source = work / "source"; output = work / "candidate"
    cache = tmp_path / "existing-owned-cache"; cache.mkdir()
    old_source = cache / "source"; old_source.mkdir()
    sentinel = old_source / "retained.rs"; sentinel.write_bytes(b"retained previous build source")
    for name in ("cargo","target"):
        (cache / name).mkdir()
    selected = build.cache_directory(work,source,output,cache)
    env = build.build_environment(work,tmp_path / "rust",selected)
    assert env["CARGO_HOME"] == str(cache / "cargo")
    assert env["CARGO_TARGET_DIR"] == str(cache / "target")
    assert sentinel.read_bytes() == b"retained previous build source"
    assert not work.exists(), "The independent source/output/ownership root must not move or be created here"


@pytest.mark.parametrize("location",["checkout","checkout-child","checkout-parent","source","source-child","source-parent","output","output-child","missing","file","cargo-file","target-file"])
def test_explicit_cache_admission_rejects_overlap_or_invalid_entries(tmp_path,monkeypatch,location):
    repo = tmp_path / "repo"; repo.mkdir()
    work = tmp_path / "work"; source = work / "source"; output = tmp_path / "output"
    source.mkdir(parents=True); output.mkdir()
    monkeypatch.setattr(build,"REPO",repo)
    choices = {"checkout":repo, "checkout-child":repo / "cache", "checkout-parent":tmp_path,
               "source":source, "source-child":source / "cache", "source-parent":work,
               "output":output, "output-child":output / "cache", "missing":tmp_path / "missing",
               "file":tmp_path / "file", "cargo-file":tmp_path / "cargo-file",
               "target-file":tmp_path / "target-file"}
    # A different enclosing source directory must fail; selecting the ordinary
    # work root deliberately preserves default layout, so use an intermediate.
    if location == "source-parent":
        source = source / "nested-source"
        cache = source.parent
    else:
        cache = choices[location]
    if location == "file":
        cache.write_bytes(b"not a directory")
    elif location != "missing" and not cache.exists():
        cache.mkdir(parents=True)
    if location in ("cargo-file","target-file"):
        (cache / location.split("-")[0]).write_bytes(b"not a directory")
    with pytest.raises(ValueError,match="cache"):
        build.cache_directory(work,source,output,cache)


def test_explicit_work_cache_preserves_default_layout(tmp_path):
    work = tmp_path / "work"; work.mkdir()
    assert build.cache_directory(work,work / "source",work / "candidate",work) == work


@pytest.mark.parametrize("jobs", [None, 1])
def test_dry_run_records_explicit_cache_without_mutating_work(tmp_path,monkeypatch,capsys,jobs):
    data = b"fixed source fixture"; archive = tmp_path / "source.tar.gz"; archive.write_bytes(data)
    work = tmp_path / "new-work"; cache = tmp_path / "existing-cache"; cache.mkdir()
    toolchain = tmp_path / "rust"
    lock = {"schema_version":1, "release_environment":build.PROFILE,
            "archive_sha256":hashlib.sha256(data).hexdigest(), "patches":[],
            "binaries":["codex.exe"], "rust_version":"1.99.1", "target":"x86_64-pc-windows-msvc",
            "upstream_commit":"fixed", "native_version":"fixture", "prepared_source_sha256":"1"*64}
    lock_path = tmp_path / "lock.json"; lock_path.write_text(json.dumps(lock))
    monkeypatch.setattr(build,"checked_output",lambda cmd:
        "rustc 1.99.1 (fixture)\nhost: x86_64-pc-windows-msvc" if cmd[0].endswith("rustc.exe")
        else "cargo 1.99.1 (fixture)")
    monkeypatch.setattr(build.shutil,"which",lambda name:str(tmp_path / name))
    monkeypatch.setenv("LIB","fixture SDK")
    assert build.main(["--archive",str(archive),"--work-dir",str(work),"--cache-dir",str(cache),
                       "--toolchain-bin",str(toolchain),"--build-lock",str(lock_path),"--dry-run"]
                      + ([] if jobs is None else ["--jobs",str(jobs)])) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["source"] == str(work / "source") and plan["output"] == str(work / "candidate")
    assert plan["work_dir"] == str(work) and plan["cache_dir"] == str(cache)
    assert plan["cargo_home"] == str(cache / "cargo") and plan["target"] == str(cache / "target")
    assert plan["runtime_executed"] is False and plan["app_qualified"] is False
    assert {key:plan[key] for key in build.build_jobs_policy(jobs)} == build.build_jobs_policy(jobs)
    assert ("--jobs" in plan["commands"][0]) is (jobs is not None)
    assert not work.exists()
    assert list(cache.iterdir()) == []


def owner_transition_fixture(tmp_path):
    work = tmp_path / "work"; source = work / "source"; cache = tmp_path / "cache"
    (source / "codex-rs").mkdir(parents=True); cache.mkdir()
    (source / "codex-rs/Cargo.lock").write_bytes(b"fixed-lock")
    (source / "lib.rs").write_bytes(b"new-implementation")
    state = build.source_state(source)
    old, new = "a" * 64, "b" * 64
    build.atomic(work / "build-owner.json", {"schema_version": 1, "build_lock_sha256": old})
    build.atomic(work / "build-report.json", {"status": "passed", "build_lock_sha256": old,
                 "source": str(source), "cache_dir": str(cache), "profile": build.PROFILE})
    build.atomic(work / "source-manifest.json", {"historical": "immutable prior record"})
    lock = {"prepared_source_sha256": state["sha256"], "prepared_source_files": state["count"],
            "cargo_lock_sha256": build.sha(source / "codex-rs/Cargo.lock")}
    plan = {"build_lock_sha256": new, "source": str(source), "cache_dir": str(cache),
            "profile": build.PROFILE, "output": str(work / "new-candidate")}
    return work, source, old, new, lock, plan


def test_owned_build_transition_preserves_source_times_and_previous_records(tmp_path):
    work, source, old, new, lock, plan = owner_transition_fixture(tmp_path)
    records = {name: (work / name).read_bytes() for name in
               ("build-owner.json", "build-report.json", "source-manifest.json")}
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.rglob("*") if p.is_file()}
    result = build.transition_owned_build(work, lock, plan, old)
    assert result["source_rewritten"] is False and result["source_timestamps_rewritten"] is False
    assert json.loads((work / "build-owner.json").read_bytes())["build_lock_sha256"] == new
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}
    for name, content in records.items():
        assert (Path(result["backup_directory"]) / name).read_bytes() == content
    assert not Path(plan["output"]).exists()


@pytest.mark.parametrize("problem", ["stale-owner", "wrong-source", "active-build", "nonempty-output"])
def test_owned_build_transition_rejects_unproven_inputs_without_owner_change(tmp_path, problem):
    work, source, old, new, lock, plan = owner_transition_fixture(tmp_path)
    if problem == "stale-owner":
        old = "c" * 64
    elif problem == "wrong-source":
        (source / "lib.rs").write_bytes(b"unexpected")
    elif problem == "active-build":
        report = json.loads((work / "build-report.json").read_bytes()); report["status"] = "building"
        build.atomic(work / "build-report.json", report)
    else:
        target = Path(plan["output"]); target.mkdir(); (target / "retain").write_bytes(b"existing")
    before = (work / "build-owner.json").read_bytes()
    with pytest.raises(ValueError):
        build.transition_owned_build(work, lock, plan, old)
    assert (work / "build-owner.json").read_bytes() == before
    assert not (work / "previous-builds").exists()


def test_owned_build_transition_A_B_A_never_rewinds_a_real_edit_timestamp(tmp_path):
    work, source, old, new, lock, plan = owner_transition_fixture(tmp_path)
    build.transition_owned_build(work, lock, plan, old)
    build.atomic(work / "build-report.json", {"status": "passed", "build_lock_sha256": new,
                 "source": str(source), "cache_dir": plan["cache_dir"], "profile": build.PROFILE})
    build.atomic(work / "source-manifest.json", build.source_state(source))
    (source / "lib.rs").write_bytes(b"prior-implementation-restored-as-a-new-edit")
    before = (source / "lib.rs").stat().st_mtime_ns
    state = build.source_state(source); lock = {**lock, "prepared_source_sha256": state["sha256"]}
    plan = {**plan, "build_lock_sha256": old, "output": str(work / "another-candidate")}
    build.transition_owned_build(work, lock, plan, new)
    assert (source / "lib.rs").stat().st_mtime_ns == before
    assert (source / "lib.rs").read_bytes() == b"prior-implementation-restored-as-a-new-edit"


def test_owned_build_transition_A_B_A_C_preserves_distinct_record_snapshots(tmp_path):
    work, source, old, new, lock, plan = owner_transition_fixture(tmp_path)
    snapshots = []
    current = old
    for index, destination in enumerate((new, old, "c" * 64)):
        build.atomic(work / "build-report.json", {"status": "passed", "build_lock_sha256": current,
                     "source": str(source), "cache_dir": plan["cache_dir"], "profile": build.PROFILE,
                     "run_index": index})
        build.atomic(work / "source-manifest.json", {**build.source_state(source), "run_index": index})
        plan = {**plan, "build_lock_sha256": destination, "output": str(work / ("output-" + str(index)))}
        result = build.transition_owned_build(work, lock, plan, current)
        snapshots.append(Path(result["backup_directory"]))
        current = destination
    assert len(set(snapshots)) == 3
    assert [json.loads((p / "build-report.json").read_bytes())["run_index"] for p in snapshots] == [0, 1, 2]


@pytest.mark.parametrize("redirect", ["backup-junction", "owner-hardlink"])
def test_owned_build_transition_refuses_redirected_or_linked_metadata(tmp_path, redirect):
    import os
    import subprocess
    work, source, old, new, lock, plan = owner_transition_fixture(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir()
    original = (work / "build-owner.json").read_bytes()
    if redirect == "owner-hardlink":
        alias = outside / "owner.json"; os.link(work / "build-owner.json", alias)
    elif os.name == "nt":
        subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(work / "previous-builds"), str(outside)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    else:
        (work / "previous-builds").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        build.transition_owned_build(work, lock, plan, old)
    assert (work / "build-owner.json").read_bytes() == original
    if redirect == "owner-hardlink":
        assert alias.read_bytes() == original
    else:
        assert list(outside.iterdir()) == []


def test_owned_build_transition_owner_lock_contention_preserves_all_records(tmp_path):
    import os
    import subprocess
    import sys
    work, source, old, new, lock, plan = owner_transition_fixture(tmp_path)
    names = ("build-owner.json", "build-report.json", "source-manifest.json")
    before = {name: (work / name).read_bytes() for name in names}
    # This is a distinct process, so the assertion proves the native OS lock
    # blocks a competing owner transition rather than only a Python object lock.
    child = (
        "import importlib.util,json,pathlib,sys;"
        "s=importlib.util.spec_from_file_location('build',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "m.transition_owned_build(pathlib.Path(sys.argv[2]),json.loads(sys.argv[3]),"
        "json.loads(sys.argv[4]),sys.argv[5])"
    )
    with (work / "build-owner.json").open("r+b", buffering=0) as handle:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            outcome = subprocess.run(
                [sys.executable, "-I", "-B", "-c", child, str(build.__file__),
                 str(work), json.dumps(lock), json.dumps(plan), old],
                capture_output=True, text=True, timeout=15)
            assert outcome.returncode != 0
            assert "locking" in outcome.stderr or "would block" in outcome.stderr or "PermissionError" in outcome.stderr
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    assert before == {name: (work / name).read_bytes() for name in names}
    assert not (work / "previous-builds").exists()


@pytest.mark.parametrize("record", ["build-report.json", "source-manifest.json"])
def test_owned_build_transition_refuses_hardlinked_preserved_records(tmp_path, record):
    import os
    work, source, old, new, lock, plan = owner_transition_fixture(tmp_path)
    alias = tmp_path / ("outside-" + record)
    os.link(work / record, alias)
    names = ("build-owner.json", "build-report.json", "source-manifest.json")
    before = {name: (work / name).read_bytes() for name in names}
    with pytest.raises(ValueError, match="single regular file"):
        build.transition_owned_build(work, lock, plan, old)
    assert before == {name: (work / name).read_bytes() for name in names}
    assert alias.read_bytes() == before[record]
    assert not (work / "previous-builds").exists()


@pytest.mark.parametrize("jobs", [1, 2])
def test_cargo_jobs_override_preserves_locked_profile_and_targets(tmp_path, jobs):
    command, = build.build_commands(Path("fixed-rust"), list(build.NATIVE_TARGETS), jobs=jobs)
    assert command[:6] == [str(Path("fixed-rust") / "cargo.exe"), "build", "--locked", "--release", "--jobs", str(jobs)]
    assert command.count("-p") == 2 and command.count("--bin") == 4 and "--bins" not in command
    env = build.build_environment(tmp_path, Path("fixed-rust"))
    assert env["CARGO_BUILD_JOBS"] == build.PROFILE["CARGO_BUILD_JOBS"] == "2"
    assert build.build_jobs_policy(jobs) == {
        "locked_max_build_jobs": 2, "effective_build_jobs": jobs, "build_jobs_override": jobs}
    assert build.build_jobs_policy() == {
        "locked_max_build_jobs": 2, "effective_build_jobs": 2, "build_jobs_override": None}


@pytest.mark.parametrize("jobs", ["0", "3"])
def test_jobs_cli_rejects_outside_lock_before_any_tool_or_write(tmp_path, monkeypatch, jobs):
    monkeypatch.setattr(build, "checked_output", lambda command: pytest.fail("tool must not run"))
    with pytest.raises(SystemExit) as error:
        build.main(["--archive", str(tmp_path / "absent.tar.gz"),
                    "--work-dir", str(tmp_path / "untouched"),
                    "--toolchain-bin", str(tmp_path / "absent-rust"),
                    "--jobs", jobs, "--dry-run"])
    assert error.value.code == 2 and not (tmp_path / "untouched").exists()


def test_job_policy_rejects_json_boolean_as_integer():
    with pytest.raises(ValueError, match="locked maximum"):
        build.build_jobs_policy(True)



def test_p8_source_candidate_preserves_exact_p7_and_uses_rich_inventory_identity():
    native = Path(__file__).parents[1] / "native"
    original = (native / "build-lock.O165-P7-original.json").read_bytes()
    assert hashlib.sha256(original).hexdigest() == "da4e194baa449c3caa4457021770876d4ddc5d663869995bd0fb1cae6eb497e6"
    p7 = json.loads(original)
    p8 = json.loads((native / "build-lock.O165-P8-source.json").read_bytes())
    assert len(p7["patches"]) == 7 and p8["patches"][:7] == p7["patches"]
    changed = {"patches", "prepared_source_sha256", "prepared_source_files", "cargo_lock_sha256"}
    assert {k:v for k,v in p8.items() if k not in changed} == {
        k:v for k,v in p7.items() if k not in changed}
    assert p8["patches"][-2:] == [
        {"file":"cadsg-request-capacity-port-0165.patch",
         "sha256":"0804fb004cbb8bcba5fcf1e29e542ee29c002c49ce3f05818eb7dbde76d3e564"},
        {"file":"cadsg-request-capacity-cancel-0165.patch",
         "sha256":"63beeb01233e21431513fcdb063e7565423790e731febfce12b249bb2e3ee225"},
    ]
    assert p8["cargo_lock_sha256"] == "24ec41c0cad0145b14a2cda3a5b343540dfcb7043c24bef32a65ae46edc7f980"
    candidate = json.loads((native / "capacity-P8-source-candidate.json").read_bytes())
    assert candidate["candidate_lock_sha256"] == hashlib.sha256((native / "build-lock.O165-P8-source.json").read_bytes()).hexdigest()
    assert candidate["prepared_source_sha256"] == p8["prepared_source_sha256"]
    assert candidate["prepared_source_files"] == 9518 and candidate["scope_union_paths"] == 23
    assert candidate["app_gui_qualified"] is False and candidate["native_runtime_qualified"] is False
    assert candidate["compiled"] is False and candidate["loaded_P7_changed"] is False
    # No prepare/build is invoked here; real source/native/App gates stay separate.



def test_p9_host_source_proposal_preserves_exact_p8_and_remains_unqualified():
    native = Path(__file__).parents[1] / "native"
    p8_raw = (native / "build-lock.O165-P8-source.json").read_bytes()
    assert hashlib.sha256(p8_raw).hexdigest() == "9d918cfc5a694b91463cf724deed26bfaecce1a48bea63c362c2814f1eb7fcb4"
    p8 = json.loads(p8_raw)
    p9_raw = (native / "build-lock.json").read_bytes()
    p9 = json.loads(p9_raw)
    assert len(p9["patches"]) == 10 and p9["patches"][:9] == p8["patches"]
    changed = {"patches", "prepared_source_sha256"}
    assert {k:v for k,v in p9.items() if k not in changed} == {
        k:v for k,v in p8.items() if k not in changed}
    assert p9["patches"][-1] == {
        "file":"windows-host-boundaries-0165.patch",
        "sha256":"c92180906b471db6c39ecbd057cb928b1071153c1fe57c02bb69cf6ffb0656df"}
    candidate = json.loads((native / "host-P9-source-candidate.json").read_bytes())
    assert candidate["candidate_lock_sha256"] == hashlib.sha256(p9_raw).hexdigest()
    assert candidate["prepared_source_sha256"] == p9["prepared_source_sha256"]
    assert candidate["prepared_source_files"] == 9518
    assert candidate["host_0165_patch_sha256"] == p9["patches"][-1]["sha256"]
    paths = {row["path"] for row in candidate["host_scope"]}
    assert paths == {"codex-rs/windows-sandbox-rs/src/" + name for name in (
        "private_acl_lock.rs", "identity.rs", "setup.rs", "desktop.rs",
        "elevated/runner_client.rs")}
    assert len(candidate["required_native_mechanism_nodes"]) == 16
    assert len(set(candidate["required_native_mechanism_nodes"])) == 16
    assert candidate["native_nodes_source_derived_not_binary_verified"] is True
    assert candidate["compiled"] is False and candidate["native_unit_executed"] is False
    assert candidate["service_station_qualified"] is False and candidate["App_GUI_qualified"] is False


def test_p10_capacity_source_candidate_preserves_p9_and_release_boundary():
    native = Path(__file__).parents[1] / "native"
    p9_raw = (native / "build-lock.O165-P9-source.json").read_bytes()
    assert hashlib.sha256(p9_raw).hexdigest() == "b50a9d41b30c902a77fa4fa7193b5a4e1a1d62117341c73c5768bf407b7af309"
    assert (native / "build-lock.json").read_bytes() == p9_raw
    p9 = json.loads(p9_raw)
    path = native / "build-lock.O165-P10-source.json"
    p10_raw = path.read_bytes()
    p10 = json.loads(p10_raw)
    report = json.loads((native / "capacity-P10-source-candidate.json").read_bytes())
    assert len(p10["patches"]) == 11 and p10["patches"][:-1] == p9["patches"]
    assert p10["patches"][-1] == {
        "file": "cadsg-capacity-p35-projection-reset-0165.patch",
        "sha256": "420eaaefb578ea811e1b58d9fe7ecbf8404340e39357b306f4e496393d602cca"}
    changed = {"patches", "prepared_source_sha256", "prepared_source_files"}
    assert {k:v for k,v in p10.items() if k not in changed} == {
        k:v for k,v in p9.items() if k not in changed}
    assert p10["prepared_source_sha256"] == "4906fe2eb05e93728f5737e4b15c57bd3e03475439951e17dfc79f50de16cd03"
    assert p10["prepared_source_files"] == 9524
    assert report["candidate_lock_sha256"] == hashlib.sha256(p10_raw).hexdigest()
    assert report["prepared_source_sha256"] == p10["prepared_source_sha256"]
    assert report["prepared_source_files"] == p10["prepared_source_files"]
    assert report["cargo_lock_sha256"] == p9["cargo_lock_sha256"]
    for row in p10["patches"]:
        assert build.sha(native / row["file"]) == row["sha256"]
    paths = {row["path"] for row in report["scope"]}
    core = {"compact.rs", "compact_capacity_projection.rs", "compact_capacity_projection_tests.rs",
            "compact_tests.rs", "compact_token_budget.rs", "session/mod.rs", "session/turn.rs",
            "session/capacity_reset_continuation.rs", "session/capacity_reset_continuation_tests.rs"}
    platform = {"desktop.rs", "service_station.rs", "service_station_tests.rs"}
    assert paths == {"codex-rs/core/src/" + name for name in core} | {
        "codex-rs/windows-sandbox-rs/src/" + name for name in platform}
    assert sum(row["before_sha256"] is None for row in report["scope"]) == 6
    assert len(report["required_native_mechanism_nodes"]) == 67
    assert len(set(report["required_native_mechanism_nodes"])) == 67
    assert report["native_nodes_source_derived_not_binary_verified"] is True
    for field in ("compiled", "native_unit_executed", "native_runtime_qualified",
                  "App_GUI_qualified", "active_P9_lock_changed", "loaded_P7_changed",
                  "release_gate_opened", "pure_protocol_dependency_changed",
                  "cds_runtime_launcher_store_supervisor_copied", "new_runtime_tool_or_agent_loop"):
        assert report[field] is False
    spec = importlib.util.spec_from_file_location("p10_release_boundary",
        Path(__file__).parents[1] / "scripts/package_native_release.py")
    pack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pack)
    with pytest.raises(ValueError, match="patch topology"):
        pack.single_graph(p10, native)


def test_p11_interactive_bootstrap_preserves_capacity_candidate_and_release_boundary():
    native = Path(__file__).parents[1] / "native"
    p10_raw = (native/"build-lock.O165-P10-source.json").read_bytes()
    assert hashlib.sha256(p10_raw).hexdigest() == "d8ba1cf5efaf60ea37050a7af81ababd7f759a01c833f5d054911c3f5ebcad21"
    p10 = json.loads(p10_raw)
    p11_raw = (native/"build-lock.O165-P11-source.json").read_bytes()
    p11 = json.loads(p11_raw)
    assert len(p11["patches"]) == 12 and p11["patches"][:-1] == p10["patches"]
    assert p11["patches"][-1] == {
        "file":"windows-interactive-runner-bootstrap-0165.patch",
        "sha256":"7913a0e4027e0e98296528afa4be1a0588690db11c11d02567532ba50a0259ae"}
    changed = {"patches","prepared_source_sha256"}
    assert {k:v for k,v in p11.items() if k not in changed} == {
        k:v for k,v in p10.items() if k not in changed}
    assert p11["prepared_source_files"] == 9524
    assert p11["prepared_source_sha256"] == "c9d94abbe9d64e808a427e59332f7b4af156afc01054ee0330c286e36b2f168a"
    for row in p11["patches"]:
        assert build.sha(native/row["file"]) == row["sha256"]
    report = json.loads((native/"interactive-P11-source-candidate.json").read_bytes())
    capacity = json.loads((native/"capacity-P10-source-candidate.json").read_bytes())
    assert report["candidate_lock_sha256"] == hashlib.sha256(p11_raw).hexdigest()
    assert report["prepared_source_sha256"] == p11["prepared_source_sha256"]
    assert report["required_capacity_nodes"] == capacity["required_native_mechanism_nodes"]
    assert report["required_bootstrap_node"] == "desktop::service_station::tests::actual_interactive_native_null_bootstrap_readonly_without_station_grant"
    assert {row["path"] for row in report["scope"]} == {
        "codex-rs/windows-sandbox-rs/src/"+name for name in (
            "desktop.rs","elevated/runner_client.rs","service_station_tests.rs")}
    assert report["same_before_bytes_as_qualified0164_sdk"] is True
    assert report["qualified_0164_sdk_is_not_native0165_or_App_qualification"] is True
    assert report["no_20022_visible_station_grant"] is True
    for name in ("compiled","native_unit_executed","native_runtime_qualified",
                 "App_GUI_qualified","active_P9_lock_changed","loaded_P7_changed",
                 "release_gate_opened","pure_protocol_dependency_changed"):
        assert report[name] is False
    assert hashlib.sha256((native/"build-lock.json").read_bytes()).hexdigest() == "b50a9d41b30c902a77fa4fa7193b5a4e1a1d62117341c73c5768bf407b7af309"
    spec = importlib.util.spec_from_file_location("p11_release_boundary",
        Path(__file__).parents[1]/"scripts/package_native_release.py")
    pack = importlib.util.module_from_spec(spec);spec.loader.exec_module(pack)
    with pytest.raises(ValueError,match="patch topology"):
        pack.single_graph(p11,native)


def test_own_runtime_package_targets_are_explicit_and_same_native_graph():
    expected = {
        **build.NATIVE_TARGETS,
        "codex-windows-sandbox-service.exe": ("codex-windows-sandbox-service", "codex-windows-sandbox-service"),
        "codex-code-mode-host.exe": ("codex-code-mode-host", "codex-code-mode-host"),
    }
    assert build.NATIVE_PACKAGE_TARGETS == expected
    names = list(expected)
    with pytest.raises(ValueError):
        build.build_commands(Path("rust"), names)
    command, = build.build_commands(Path("rust"), names, jobs=1, runtime_package=True)
    assert command.count("-p") == 4 and command.count("--bin") == 6 and "--bins" not in command
    assert command[:6] == [str(Path("rust")/"cargo.exe"),"build","--locked","--release","--jobs","1"]
    for invalid in [names[:-1],names+[names[-1]],["codex-windows-sandbox-service.exe"]]:
        with pytest.raises(ValueError):
            build.build_commands(Path("rust"), invalid, runtime_package=True)


def test_p12_own_runtime_service_source_keeps_normal_profile_and_release_gate_closed():
    native = Path(__file__).parents[1] / "native"
    p11 = json.loads((native / "build-lock.O165-P11-source.json").read_bytes())
    p12_raw = (native / "build-lock.O165-P12-source.json").read_bytes()
    p12 = json.loads(p12_raw)
    assert p12_raw == (json.dumps(p12, indent=2) + "\n").encode()
    assert p12["release_environment"] == p11["release_environment"] == build.PROFILE
    assert len(p12["patches"]) == 13 and p12["patches"][:-1] == p11["patches"]
    assert p12["package_mode"] == "cadsg-native-msix"
    assert set(p12["binaries"]) == set(build.NATIVE_PACKAGE_TARGETS)
    assert p12["prepared_source_files"] == 9525
    assert p12["prepared_source_sha256"] == "503a27225346c4017bcf060909325c7a46d4fccd84c64db34d87340328d35d0e"
    for row in p12["patches"]:
        assert build.sha(native / row["file"]) == row["sha256"]
    command, = build.build_commands(Path("rust"), p12["binaries"], jobs=1, runtime_package=True)
    assert command.count("--bin") == 6 and "--bins" not in command
    report = json.loads((native / "runtime-service-P12-source-candidate.json").read_bytes())
    assert report["candidate_lock_sha256"] == hashlib.sha256(p12_raw).hexdigest()
    assert len(report["scope"]) == 38
    assert sum(row["before"] is None for row in report["scope"]) == 1
    assert report["normal_build_profile_unchanged"] is True
    assert report["native_package_name"] == "CADSG.NativeRuntime"
    assert report["registry_key"] == r"SOFTWARE\CADSG\WindowsSandboxService"
    for flag in ("compiled", "native_unit_executed", "native_runtime_qualified", "App_GUI_qualified",
                 "release_gate_opened", "active_P9_lock_changed", "loaded_P7_changed", "stock_owner_record_changed",
                 "stock_App_changed", "deployment_performed", "trust_or_certificate_changed"):
        assert report[flag] is False
    assert hashlib.sha256((native / "build-lock.json").read_bytes()).hexdigest() == "b50a9d41b30c902a77fa4fa7193b5a4e1a1d62117341c73c5768bf407b7af309"
    spec = importlib.util.spec_from_file_location("p12_release_boundary", Path(__file__).parents[1] / "scripts/package_native_release.py")
    pack = importlib.util.module_from_spec(spec); spec.loader.exec_module(pack)
    # Historical P12 is rejected at the newer six-program exact-lock admission.
    with pytest.raises(ValueError, match=r"^Six-target source bundle must match an explicitly reviewed P16 lock or reviewed P17 lock or reviewed P18 lock or reviewed P19 lock or reviewed P20 lock or reviewed P21 lock$"):
        pack.single_graph(p12, native)
    # Reducing the output declaration cannot bypass the independent source gate.
    with pytest.raises(ValueError, match="patch topology"):
        pack.single_graph({**p12, "binaries": list(build.NATIVE_TARGETS)}, native)


def test_p13_repairs_service_roles_and_cleanup_without_changing_native_plan_or_profile():
    native = Path(__file__).parents[1] / "native"
    p12 = json.loads((native / "build-lock.O165-P12-source.json").read_bytes())
    raw = (native / "build-lock.O165-P13-source.json").read_bytes()
    p13 = json.loads(raw)
    assert raw == (json.dumps(p13, indent=2) + "\n").encode()
    assert {k: v for k, v in p13.items() if k not in {"patches", "prepared_source_sha256"}} == {
        k: v for k, v in p12.items() if k not in {"patches", "prepared_source_sha256"}}
    assert len(p13["patches"]) == 14 and p13["patches"][:-1] == p12["patches"]
    assert p13["prepared_source_sha256"] == "bc80e68da4705fb1e6761fca0e4990078bded9201d42e8551f7e449114e8be31"
    assert p13["prepared_source_files"] == 9525 and p13["release_environment"] == build.PROFILE
    for row in p13["patches"]:
        assert build.sha(native / row["file"]) == row["sha256"]
    report = json.loads((native / "runtime-service-P13-source-candidate.json").read_bytes())
    prior = json.loads((native / "runtime-service-P12-source-candidate.json").read_bytes())
    assert report["candidate_lock_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["same_73_planned_native_names"] == prior["required_native_nodes_source_derived"]
    assert len(report["same_73_planned_native_names"]) == 73
    assert {row["path"] for row in report["scope"]} == {
        "codex-rs/windows-sandbox-rs/src/" + x for x in (
            "namespace_coordination.rs", "uninstall_windows.rs", "runtime_ownership_tests.rs")}
    assert report["coordination_mutex_enabled_authority_unchanged"] is True
    assert report["cleanup_home_compares_same_canonical_form"] is True
    assert report["missing_home_record_only_cleanup_preserved"] is True
    assert report["original_P12_source_and_evidence_preserved"] is True
    for name in ("compiled", "native_unit_executed", "App_GUI_qualified", "release_gate_open", "active_P9_changed"):
        assert report[name] is False


def test_p14_finalizer_and_doctor_contract_keeps_prior_source_and_release_boundaries():
    native = Path(__file__).parents[1] / "native"
    p13 = json.loads((native / "build-lock.O165-P13-source.json").read_bytes())
    raw = (native / "build-lock.O165-P14-source.json").read_bytes(); p14 = json.loads(raw)
    assert raw == (json.dumps(p14, indent=2) + "\n").encode()
    assert {k:v for k,v in p14.items() if k not in {"patches", "prepared_source_sha256"}} == {
        k:v for k,v in p13.items() if k not in {"patches", "prepared_source_sha256"}}
    assert len(p14["patches"]) == 15 and p14["patches"][:-1] == p13["patches"]
    assert p14["prepared_source_sha256"] == "61974cd36d19f0f8ff5b5172986658d5a415051b2999d68bc0a255b3a7c972fe"
    assert p14["prepared_source_files"] == 9525 and p14["release_environment"] == build.PROFILE
    for row in p14["patches"]:
        assert build.sha(native / row["file"]) == row["sha256"]
    report = json.loads((native / "runtime-service-P14-source-candidate.json").read_bytes())
    prior = json.loads((native / "runtime-service-P13-source-candidate.json").read_bytes())
    assert report["candidate_lock_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["same_73_planned_native_names"] == prior["same_73_planned_native_names"]
    assert len(report["scope"]) == 9
    assert report["additional_existing_service_nodes"] == [
        "registered_runtime::removal::tests::finalizer_script_parses_and_native_bindings_compile",
        "registered_runtime::removal::tests::finalizer_preserves_utf8_plan_and_requires_commit_and_stdin_eof"]
    for field in ("actual_binary_list_verified", "unknown_namespace_stock_lookup_fallback",
                  "actual_PS_cleanup_body_or_SAM_invoked", "compiled", "native_unit_executed",
                  "App_GUI_qualified", "release_gate_open", "active_P9_changed"):
        assert report[field] is False
    for field in ("finalizer_all_record_accounts_exact_names_and_SID", "namespace_CAS_for_Core_and_parent",
                  "Legacy_true_missing_home_watchers_restored", "Registered_original_missing_credentials_limit_preserved",
                  "doctor_queries_only_owned_native_factory_names", "shared_native_setup_mutex_preserved"):
        assert report[field] is True
