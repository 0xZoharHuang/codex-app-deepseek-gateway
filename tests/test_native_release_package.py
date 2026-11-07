"""Packaging boundaries only; no compiler, native executable or App is run."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_release_package", ROOT / "scripts/package_native_release.py")
pack = importlib.util.module_from_spec(spec); spec.loader.exec_module(pack)


def raw():
    return {"schema_version":1, "status":"built-not-app-qualified",
            "outputs":[{"name":name, "size":len(name.encode()), "sha256":hashlib.sha256(name.encode()).hexdigest()}
                       for name in sorted(pack.BINARIES)]}


@pytest.mark.parametrize("kind", ["prepared", "extra", "missing", "path"])
def test_public_native_admission_refuses_prepared_or_unexpected_outputs(kind):
    value = raw()
    if kind == "prepared":
        value["status"] = "prepared-not-app-qualified"
    elif kind == "extra":
        value["outputs"].append({"name":"codex-code-mode-host.exe", "source":{"kind":"official_app_package"}})
    elif kind == "missing":
        value["outputs"].pop()
    else:
        value["outputs"][0]["path"] = r"C:\Program Files\WindowsApps\official.exe"
    with pytest.raises(ValueError):
        pack.raw_outputs(value)


def test_N_J_common_graph_requires_exact_locked_inputs():
    n = pack.load(ROOT / "native/build-lock.N.json")
    j = pack.load(ROOT / "native/build-lock.J.json")
    proof = pack.shared_graph(n, j, ROOT / "native")
    assert proof["producer_sources_remain_separate"] is True
    assert all(path.endswith(".rs") for path in proof["N_only_changed_paths"])
    n["cargo_lock_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="dependency input"):
        pack.shared_graph(n, j, ROOT / "native")


def metadata(tmp_path, license_name="MIT"):
    cache = tmp_path / "cache"; crate = cache / "example"; crate.mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname="example"\nversion="1.0"\n')
    (crate / "LICENSE").write_text("Original fixture license notice\n")
    nested = crate / "third_party/licenses"; nested.mkdir(parents=True)
    (nested / "NOTICE").write_text("Nested original copyright notice\n")
    return cache, crate, {"packages":[{"id":"registry+https://index.crates.io/#example@1.0",
             "name":"example", "version":"1.0", "source":"registry+https://index.crates.io/",
             "manifest_path":str(crate / "Cargo.toml"), "license":license_name,
             "authors":["Public Fixture Author"], "repository":"https://example.invalid/source"}]}


def test_native_notice_collection_retains_nested_bytes_and_attribution(tmp_path):
    cache, crate, value = metadata(tmp_path)
    index = pack.collect_licenses(value, tmp_path / "out", [cache])
    item = index["packages"][0]
    assert item["declared_license"] == "MIT" and item["authors"] == ["Public Fixture Author"]
    assert len(item["upstream_notice_files"]) == 2
    for row in item["upstream_notice_files"]:
        path = tmp_path / "out" / row["file"]
        assert pack.sha(path) == row["sha256"] and path.stat().st_nlink == 1
    assert str(cache) not in json.dumps(index)


@pytest.mark.parametrize("kind", ["unknown-license", "outside-source"])
def test_native_notice_collection_refuses_unpinned_or_external_file(tmp_path, kind):
    cache, crate, value = metadata(tmp_path)
    if kind == "unknown-license":
        value["packages"][0]["license"] = "LicenseRef-Unpinned"
    else:
        private = tmp_path / "not-a-crate-license.txt"; private.write_text("private fixture")
        value["packages"][0]["license_file"] = str(private)
    with pytest.raises(ValueError):
        pack.collect_licenses(value, tmp_path / "out", [cache])


def staging(tmp_path):
    stage = tmp_path / "native"; stage.mkdir()
    for row in raw()["outputs"]:
        (stage / row["name"]).write_bytes(row["name"].encode())
    pack.write(stage / "manifest.json", raw())
    for name in ["LICENSE", "NOTICE", "licenses/codex-LICENSE", "licenses/codex-NOTICE"]:
        p = stage / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("license fixture")
    pack.write(stage / "licenses/rust/index.json", {"packages":[]})
    pack.write(stage / "provenance/producer-references.json", {"scope":"fixture"})
    return stage


def test_native_archive_exactly_four_binaries_and_reproducible_metadata(tmp_path):
    stage = staging(tmp_path)
    a, b = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    first, second = pack.make_archive(stage, a, 1234567890), pack.make_archive(stage, b, 1234567890)
    assert first == second
    with tarfile.open(a) as archive:
        assert set(name for name in archive.getnames() if name.endswith(".exe")) == pack.BINARIES
        assert all(m.isfile() for m in archive.getmembers())


@pytest.mark.parametrize("kind", ["sidecar", "private-data", "native-tamper"])
def test_native_archive_refuses_extra_sidecar_private_metadata_or_tamper(tmp_path, kind):
    stage = staging(tmp_path)
    if kind == "sidecar":
        (stage / "codex-code-mode-host.exe").write_text("not-for-redistribution fixture")
    elif kind == "private-data":
        pack.write(stage / "provenance/credentials.json", {"fixture":"not public"})
    else:
        (stage / "codex.exe").write_text("changed")
    with pytest.raises(ValueError):
        pack.make_archive(stage, tmp_path / "rejected.tar.gz", 1234567890)


@pytest.mark.parametrize("corruption", [None, "replace-index-hash-with-N-tree", "wrong-sibling-producer"])
def test_mixed_source_index_preserves_existing_manifest_semantics(corruption):
    n = pack.load(ROOT / "native/build-lock.N.json")
    j = pack.load(ROOT / "native/build-lock.J.json")
    value = raw(); digest = "1" * 64; refs = "2" * 64
    for row in value["outputs"]:
        row["producer_id"] = "N-CLI" if row["name"] == "codex.exe" else "J17"
    value.update(source_sha256=digest, source_index={"file":"native-source-index.json", "sha256":digest})
    index = {"kind":"per_binary_native_source_index",
             "producer_references":{"file":"producer-references.json","sha256":refs},
             "producer_sources":{"N-CLI":{"source_sha256":n["prepared_source_sha256"]},
                                 "J17":{"source_sha256":j["prepared_source_sha256"]}},
             "binary_producers":copy.deepcopy(value["outputs"])}
    if corruption == "replace-index-hash-with-N-tree":
        value["source_sha256"] = n["prepared_source_sha256"]
    elif corruption == "wrong-sibling-producer":
        next(row for row in index["binary_producers"] if row["name"] != "codex.exe")["producer_id"] = "N-CLI"
    if corruption is None:
        pack.indexed_sources(value, index, digest, refs, n, j)
    else:
        with pytest.raises(ValueError):
            pack.indexed_sources(value, index, digest, refs, n, j)


@pytest.mark.parametrize("relative", ["native-source-index.json", "provenance/native-source-index.json"])
def test_source_index_location_accepts_raw_and_public_nested_layout(tmp_path, relative):
    path = tmp_path / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("{}")
    assert pack.source_index_location(tmp_path / "manifest.json", {"source_index":{"file":relative}}) == path


@pytest.mark.parametrize("relative", ["../native-source-index.json", r"provenance\native-source-index.json", "/native-source-index.json"])
def test_source_index_location_rejects_escape_or_ambiguous_path(tmp_path, relative):
    with pytest.raises(ValueError):
        pack.source_index_location(tmp_path / "manifest.json", {"source_index":{"file":relative}})


def single_recipe(tmp_path):
    native = tmp_path / "native"; native.mkdir()
    patches = []
    for name in ("windows-home-namespace-0165.patch", "mcp-keyring-isolation-0165.patch",
                 "guardian-thread-context-0165.patch", "windows-cwd-ancestor-metadata-0165.patch"):
        path = native / name; path.write_text("reviewed fixture " + name)
        patches.append({"file":name, "sha256":pack.sha(path)})
    build_spec = importlib.util.spec_from_file_location("native_build_single_fixture", ROOT / "scripts/build_native.py")
    build = importlib.util.module_from_spec(build_spec); build_spec.loader.exec_module(build)
    lock = {"schema_version":1, "candidate_revision":"O165",
            "upstream_commit":"720fec862a608ae1e5878d1eecb04fb93192e93c",
            "native_version":"0.165.0-alpha.1.2", "target":pack.TARGET,
            "rust_version":"1.99.1", "archive_sha256":"1"*64,
            "prepared_source_sha256":"2"*64, "prepared_source_files":9514,
            "cargo_lock_sha256":"3"*64, "binaries":sorted(pack.BINARIES),
            "release_environment":copy.deepcopy(build.PROFILE), "patches":patches}
    lock_digest = hashlib.sha256(pack.canonical(lock)).hexdigest()
    value = raw(); value.update(candidate_revision="O165", build_lock_sha256=lock_digest,
        upstream_commit=lock["upstream_commit"], native_version=lock["native_version"],
        archive_sha256=lock["archive_sha256"], source_sha256=lock["prepared_source_sha256"],
        patches=copy.deepcopy(patches), profile=copy.deepcopy(build.PROFILE),
        commands=build.build_commands(Path("private-rust"),lock["binaries"]),
        work_dir=str(tmp_path / "private-work"), target=str(tmp_path / "private-work/target"),
        cargo_home=str(tmp_path / "private-work/cargo"))
    return native, lock, lock_digest, value, build


def test_historical_p7_pinned_single_producer_recipe_is_admitted_by_packager():
    # Retain the exact reviewed P7 source/patch graph while P8 is source-only.
    path = ROOT / "native/build-lock.O165-P7-original.json"
    assert pack.sha(path) == "da4e194baa449c3caa4457021770876d4ddc5d663869995bd0fb1cae6eb497e6"
    lock = pack.load(path)
    graph = pack.single_graph(lock, ROOT / "native")
    assert graph["producer_id"] == "O165"
    assert graph["producer_sources_remain_separate"] is False


def test_single_producer_projects_exact_four_identities_without_private_paths(tmp_path):
    native, lock, digest, value, build = single_recipe(tmp_path)
    graph = pack.single_graph(lock,native)
    references,index,public = pack.single_sources(value,lock,digest,"4"*64,build)
    assert graph["producer_id"] == "O165" and graph["producer_sources_remain_separate"] is False
    assert references["original_raw_manifest_sha256"] == "4"*64
    assert references["source_sha256"] == index["source_sha256"] == public["source_sha256"] == lock["prepared_source_sha256"]
    assert set(pack.raw_outputs(public)) == pack.BINARIES
    assert {row["producer_id"] for row in public["outputs"]} == {"O165"}
    assert references["binaries"] == index["binary_producers"] == public["outputs"]
    serialized = json.dumps([references,index,public])
    assert str(tmp_path) not in serialized and "private-rust" not in serialized
    assert "commands" not in public and "work_dir" not in public and "target" not in public


@pytest.mark.parametrize("corruption",["patch-bytes","patch-name","missing-guardian","guardian-bytes",
                                        "patch-order","missing-cwd-metadata","cwd-metadata-bytes","missing-target","different-upstream","different-version"])
def test_single_producer_graph_rejects_unpinned_inputs(tmp_path, corruption):
    native,lock,_,_,_ = single_recipe(tmp_path)
    if corruption == "patch-bytes":
        (native / lock["patches"][0]["file"]).write_text("tampered")
    elif corruption == "patch-name":
        lock["patches"][0]["file"] = "unreviewed.patch"
    elif corruption == "missing-guardian":
        lock["patches"].pop(2)
    elif corruption == "missing-cwd-metadata":
        lock["patches"].pop(3)
    elif corruption == "cwd-metadata-bytes":
        (native / lock["patches"][3]["file"]).write_text("tampered cwd metadata patch")
    elif corruption == "guardian-bytes":
        (native / lock["patches"][2]["file"]).write_text("tampered Guardian patch")
    elif corruption == "patch-order":
        lock["patches"][1], lock["patches"][2] = lock["patches"][2], lock["patches"][1]
    elif corruption == "missing-target":
        lock["binaries"].pop()
    elif corruption == "different-upstream":
        lock["upstream_commit"] = "0"*40
    else:
        lock["native_version"] = "0.166.0"
    with pytest.raises(ValueError):
        pack.single_graph(lock,native)


@pytest.mark.parametrize("corruption",["source","lock","patches","profile","command","target","cargo-home","extra-output"])
def test_single_producer_manifest_rejects_mismatched_builder_inputs(tmp_path,corruption):
    _,lock,digest,value,build = single_recipe(tmp_path)
    if corruption == "source":
        value["source_sha256"] = "0"*64
    elif corruption == "lock":
        value["build_lock_sha256"] = "0"*64
    elif corruption == "patches":
        value["patches"].pop()
    elif corruption == "profile":
        value["profile"]["CARGO_BUILD_JOBS"] = "99"
    elif corruption == "command":
        value["commands"][0].append("--bins")
    elif corruption == "target":
        value["target"] = str(tmp_path / "other-cache/target")
    elif corruption == "cargo-home":
        value["cargo_home"] = str(tmp_path / "other-cache/cargo")
    else:
        value["outputs"].append({"name":"codex-code-mode-host.exe","size":1,"sha256":"0"*64})
    with pytest.raises(ValueError):
        pack.single_sources(value,lock,digest,"4"*64,build)


def test_single_producer_staging_admits_exact_public_provenance_names(tmp_path):
    stage = staging(tmp_path)
    manifest = pack.load(stage / "manifest.json"); manifest["candidate_revision"] = "O165"
    (stage / "manifest.json").write_text(json.dumps(manifest))
    for name in ("O165-original-build-lock.json", "native-source-index.json", "original-raw-manifest.json"):
        pack.write(stage / "provenance" / name, {"public_fixture":True})
    admitted = pack.validate_tree(stage)
    assert "provenance/O165-original-build-lock.json" in admitted
    pack.write(stage / "provenance/unrelated-private-receipt.json", {"private_fixture":True})
    with pytest.raises(ValueError,match="Unexpected public native file"):
        pack.validate_tree(stage)

def test_single_producer_staging_refuses_old_cohort_patch(tmp_path):
    stage = staging(tmp_path)
    manifest = pack.load(stage / "manifest.json"); manifest["candidate_revision"] = "O165"
    (stage / "manifest.json").write_text(json.dumps(manifest))
    old = ROOT / "native/payload-request-capacity-replay.patch"
    path = stage / "provenance/patches" / old.name
    path.parent.mkdir(parents=True); path.write_bytes(old.read_bytes())
    with pytest.raises(ValueError,match="Unexpected public native file"):
        pack.validate_tree(stage)

def test_single_producer_explicit_cache_keeps_source_identity_and_strips_private_paths(tmp_path):
    _,lock,digest,value,build = single_recipe(tmp_path)
    original_source = value["source_sha256"]
    cache = tmp_path / "existing-owned-cache"; cache.mkdir()
    value.update(cache_dir=str(cache), target=str(cache / "target"), cargo_home=str(cache / "cargo"))
    refs,index,public = pack.single_sources(value,lock,digest,"4"*64,build)
    assert public["source_sha256"] == refs["source_sha256"] == original_source
    assert str(cache) not in json.dumps([refs,index,public])
    assert "cache_dir" not in public and "cargo_home" not in public


def test_vendored_library_notices_are_retained_outside_named_notice_folders(tmp_path):
    cache, crate, value = metadata(tmp_path)
    originals = {}
    for relative in (
        "curl/COPYING", "aws-lc/LICENSE", "aws-lc/third_party/fiat/LICENSE",
        "vendor/codec/COPYRIGHT", "vendor/codec/NOTICE.txt", "vendor/codec/LICENSE-APACHE",
        "vendor/codec/LICENCE.md", "vendor/codec/COPYING.LESSER",
        # Preserve all files in the existing dedicated notice folders.
        "licenses/author-attribution.txt",
    ):
        path = crate / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        originals[relative] = ("Original vendored notice: " + relative + "\n").encode()
        path.write_bytes(originals[relative])
    source = crate / "vendor/codec/not-a-LICENSE-source.c"
    source.write_text("source code is not an upstream notice\n")
    index = pack.collect_licenses(value, tmp_path / "out", [cache])
    rows = index["packages"][0]["upstream_notice_files"]
    copied = [(tmp_path / "out" / row["file"]).read_bytes() for row in rows]
    assert len(rows) == 2 + len(originals)
    assert all(copied.count(content) == 1 for content in originals.values())
    assert source.read_bytes() not in copied
    assert index["packages"][0]["README_fallback"] is False


@pytest.mark.parametrize("magic", [b"MZ\x00\x00", b"\x7fELF", b"PK\x03\x04"])
def test_vendored_notice_rejects_binary_or_container_bytes(tmp_path, magic):
    cache, crate, value = metadata(tmp_path)
    nested = crate / "vendor/nested/COPYING"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(magic + b"not an upstream source notice")
    with pytest.raises(ValueError, match="Compiled/container bytes"):
        pack.collect_licenses(value, tmp_path / "out", [cache])


def test_vendored_notice_scan_rejects_redirected_directory_without_following_it(tmp_path):
    import os
    import subprocess
    cache, crate, value = metadata(tmp_path)
    outside = tmp_path / "outside-crate"; outside.mkdir()
    secret = outside / "COPYING"; secret.write_bytes(b"outside fixture must not be copied")
    link = crate / "vendor"
    if os.name == "nt":
        subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"),
                        "/d", "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="redirected entry"):
            pack.collect_licenses(value, tmp_path / "out", [cache])
        assert secret.read_bytes() == b"outside fixture must not be copied"
        assert not any(p.read_bytes() == secret.read_bytes()
                       for p in (tmp_path / "out").rglob("*") if p.is_file())
    finally:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()


@pytest.mark.parametrize("bound", ["entries", "depth", "count", "bytes"])
def test_vendored_notice_scan_refuses_exceeded_bounds(tmp_path, monkeypatch, bound):
    cache, crate, value = metadata(tmp_path)
    path = crate / "vendor/a/b/COPYING"
    path.parent.mkdir(parents=True)
    path.write_text("Original bounded vendored notice\n")
    if bound == "entries":
        monkeypatch.setattr(pack, "MAX_NOTICE_SCAN_ENTRIES", 2)
    elif bound == "depth":
        monkeypatch.setattr(pack, "MAX_NOTICE_SCAN_DEPTH", 2)
    elif bound == "count":
        monkeypatch.setattr(pack, "MAX_CRATE_NOTICE_FILES", 2)
    else:
        monkeypatch.setattr(pack, "MAX_CRATE_NOTICE_BYTES", 16)
    with pytest.raises(ValueError, match="bounded"):
        pack.collect_licenses(value, tmp_path / "out", [cache])


def test_notice_collection_refuses_output_inside_source(tmp_path):
    cache, crate, value = metadata(tmp_path)
    with pytest.raises(ValueError, match="inside crate source"):
        pack.collect_licenses(value, crate / "generated-licenses", [cache])


@pytest.mark.parametrize("mode,attributes,rejected", [
    (0o100600, 0x400, True),
    (0o040700, 0x400 | 0x10, True),
    (0o120777, 0, True),
    (0o100600, 0, False),
])
def test_notice_reparse_guard_works_without_newer_junction_api(mode, attributes, rejected):
    from types import SimpleNamespace
    metadata = SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
    assert pack.redirected(Path("unused-fixture-path"), metadata) is rejected


@pytest.mark.parametrize("patch_count", [4, 5, 6, 7])
def test_single_producer_accepts_real_independent_target_without_private_projection(tmp_path, patch_count):
    native, lock, _, value, build = single_recipe(tmp_path)
    for name in ["packaged-tectonic-materialization-0165.patch",
                 "guardian-provider-transport-0165.patch",
                 "migration-checksum-eol-0165.patch"][:patch_count - 4]:
        patch = native / name
        patch.write_text("reviewed pinned component fixture")
        lock["patches"].append({"file": patch.name, "sha256": pack.sha(patch)})
    value["patches"] = copy.deepcopy(lock["patches"])
    digest = hashlib.sha256(pack.canonical(lock)).hexdigest()
    value["build_lock_sha256"] = digest
    cache = tmp_path / "independent-cargo-cache"
    (cache / "cargo").mkdir(parents=True)
    target = tmp_path / "independent-native-target"
    target.mkdir()
    value.update(cache_dir=str(cache), cargo_home=str(cache / "cargo"),
                 source=str(tmp_path / "prepared-source"), output=str(tmp_path / "native-output"),
                 target=str(build.target_directory(cache, tmp_path / "prepared-source",
                                                   tmp_path / "native-output", target)))
    pack.single_graph(lock, native)
    refs, index, public = pack.single_sources(value, lock, digest, "4" * 64, build)
    assert refs["binaries"] == index["binary_producers"] == public["outputs"]
    assert str(tmp_path) not in json.dumps([refs, index, public])


@pytest.mark.parametrize("bad_target", ["missing", "source", "output", "cargo-home", "relative", "no-source-record"])
def test_single_producer_independent_target_keeps_standard_builder_guards(tmp_path, bad_target):
    _, lock, digest, value, build = single_recipe(tmp_path)
    cache = tmp_path / "separate-cargo-cache"
    (cache / "cargo").mkdir(parents=True)
    source = tmp_path / "prepared-source"; source.mkdir()
    output = tmp_path / "candidate-output"; output.mkdir()
    target = tmp_path / "independent-target"; target.mkdir()
    value.update(cache_dir=str(cache), cargo_home=str(cache / "cargo"),
                 source=str(source), output=str(output), target=str(target))
    if bad_target == "missing":
        value["target"] = str(tmp_path / "not-created-target")
    elif bad_target == "source":
        value["target"] = str(source)
    elif bad_target == "output":
        value["target"] = str(output)
    elif bad_target == "cargo-home":
        value["target"] = str(cache / "cargo")
    elif bad_target == "relative":
        value["target"] = "relative-target"
    else:
        value.pop("source")
    with pytest.raises(ValueError):
        pack.single_sources(value, lock, digest, "4" * 64, build)


def test_single_producer_binds_effective_one_job_and_public_provenance(tmp_path):
    native, lock, digest, value, build = single_recipe(tmp_path)
    value.update(build.build_jobs_policy(1))
    value["commands"] = build.build_commands(Path("private-rust"), lock["binaries"], jobs=1)
    refs, index, public = pack.single_sources(value, lock, digest, "4"*64, build)
    assert refs["build_resources"] == public["build_resources"] == build.build_jobs_policy(1)
    assert refs["release_environment"]["CARGO_BUILD_JOBS"] == "2"
    assert "private-rust" not in json.dumps([refs, index, public])


@pytest.mark.parametrize("corruption", ["zero", "above-lock", "bool", "partial", "wrong-effective", "missing-argv", "unreported-argv"])
def test_single_producer_jobs_cannot_be_silently_misreported(tmp_path, corruption):
    native, lock, digest, value, build = single_recipe(tmp_path)
    value.update(build.build_jobs_policy(1))
    value["commands"] = build.build_commands(Path("private-rust"), lock["binaries"], jobs=1)
    if corruption == "zero":
        value["build_jobs_override"] = 0
    elif corruption == "above-lock":
        value["build_jobs_override"] = 3
    elif corruption == "bool":
        value["effective_build_jobs"] = True
    elif corruption == "partial":
        del value["locked_max_build_jobs"]
    elif corruption == "wrong-effective":
        value["effective_build_jobs"] = 2
    elif corruption == "missing-argv":
        value["commands"] = build.build_commands(Path("private-rust"), lock["binaries"])
    else:
        for key in build.build_jobs_policy(1):
            del value[key]
    with pytest.raises(ValueError):
        pack.single_sources(value, lock, digest, "4"*64, build)



@pytest.mark.parametrize("lock_name,patch_count", [
    ("build-lock.O165-P8-source.json", 9), ("build-lock.json", 10)])
def test_p8_p9_source_candidates_cannot_reuse_p7_release_packaging_gate(lock_name, patch_count):
    lock = pack.load(ROOT / "native" / lock_name)
    assert len(lock["patches"]) == patch_count
    with pytest.raises(ValueError, match="Single-producer patch topology differs"):
        pack.single_graph(lock, ROOT / "native")


def test_p16_six_target_graph_requires_exact_reviewed_lock():
    lock = pack.load(ROOT / "native/build-lock.O165-P16-source.json")
    assert set(lock["binaries"]) == pack.SOURCE_BINARIES
    proof = pack.single_graph(lock, ROOT / "native")
    assert proof["producer_id"] == "O165"
    changed = copy.deepcopy(lock)
    changed["prepared_source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="reviewed P16 lock"):
        pack.single_graph(changed, ROOT / "native")


@pytest.mark.parametrize("problem", [None, "missing", "extra", "registered", "unknown_kind"])
def test_six_target_public_bundle_keeps_exact_inventory_and_explicit_legacy_mode(problem):
    value = {"schema_version":1, "status":"built-not-app-qualified",
             "kind":pack.SOURCE_BUNDLE_KIND, "runtime_mode":"legacy",
             "outputs":[{"name":name, "size":1, "sha256":"1"*64} for name in sorted(pack.SOURCE_BINARIES)]}
    if problem == "missing": value["outputs"].pop()
    elif problem == "extra": value["outputs"].append({"name":"foreign.exe", "size":1, "sha256":"1"*64})
    elif problem == "registered": value["runtime_mode"] = "registered_core"
    elif problem == "unknown_kind": value["kind"] = "asserted-signed-runtime"
    if problem:
        with pytest.raises(ValueError): pack.raw_outputs(value)
    else:
        assert set(pack.raw_outputs(value)) == pack.SOURCE_BINARIES


@pytest.mark.parametrize("lock_name", ["build-lock.O165-P16-source.json", "build-lock.O165-P17-source.json", "build-lock.O165-P18-source.json", "build-lock.O165-P19-source.json", "build-lock.O165-P20-source.json", "build-lock.O165-P21-source.json"])
def test_reviewed_source_public_projection_and_archive_keep_all_six_producers(tmp_path, lock_name):
    _, _, _, value, build = single_recipe(tmp_path)
    lock_path = ROOT/"native"/lock_name
    lock = pack.load(lock_path)
    digest = pack.sha(lock_path)
    value.update(build_lock_sha256=digest, upstream_commit=lock["upstream_commit"],
                 native_version=lock["native_version"], archive_sha256=lock["archive_sha256"],
                 source_sha256=lock["prepared_source_sha256"], patches=copy.deepcopy(lock["patches"]),
                 profile=copy.deepcopy(lock["release_environment"]),
                 commands=build.build_commands(Path("private-rust"),lock["binaries"],runtime_package=True),
                 outputs=[{"name":name,"size":len(name.encode()),"sha256":hashlib.sha256(name.encode()).hexdigest()}
                          for name in sorted(pack.SOURCE_BINARIES)])
    refs,index,public = pack.single_sources(value,lock,digest,"4"*64,build)
    assert public["kind"] == pack.SOURCE_BUNDLE_KIND and public["runtime_mode"] == "legacy"
    assert {x["name"] for x in public["outputs"]} == pack.SOURCE_BINARIES
    assert {x["producer_id"] for x in public["outputs"]} == {"O165"}
    assert str(tmp_path) not in json.dumps([refs,index,public])
    stage = tmp_path/"six-stage"; stage.mkdir()
    for row in public["outputs"]: (stage/row["name"]).write_bytes(row["name"].encode())
    pack.write(stage/"manifest.json",public)
    for name in ["LICENSE","NOTICE","licenses/codex-LICENSE","licenses/codex-NOTICE"]:
        path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"fixture license")
    pack.write(stage/"licenses/rust/index.json",{"packages":[]})
    pack.write(stage/"provenance/producer-references.json",refs)
    pack.copy_bytes(lock_path,stage/"provenance/O165-original-build-lock.json")
    for patch in lock["patches"]: pack.copy_bytes(ROOT/"native"/patch["file"],stage/"provenance/patches"/patch["file"])
    archive=tmp_path/"six.tar.gz"
    pack.make_archive(stage,archive,1234567890,build_lock=lock_path)
    with tarfile.open(archive) as opened:
        assert {m.name for m in opened.getmembers() if m.name.endswith(".exe")} == pack.SOURCE_BINARIES
        assert all(m.isfile() for m in opened.getmembers())
    (stage/"unexpected.exe").write_bytes(b"MZnot allowed")
    with pytest.raises(ValueError,match="Unexpected public native file"):
        pack.validate_tree(stage,build_lock=lock_path)


@pytest.mark.parametrize("problem", [None, "source", "missing_patch", "foreign_patch", "extra_field", "profile"])
def test_p17_source_graph_requires_exact_new_lock_and_preserves_p16(problem):
    old_path = ROOT/"native/build-lock.O165-P16-source.json"
    assert pack.sha(old_path) == pack.P16_LOCK_SHA256
    path = ROOT/"native/build-lock.O165-P17-source.json"
    assert pack.sha(path) == pack.P17_LOCK_SHA256
    lock = pack.load(path)
    assert lock["patches"][-1]["file"] == "cadsg-native-opaque-metadata-deny-0165.patch"
    if problem == "source": lock["prepared_source_sha256"] = "0" * 64
    elif problem == "missing_patch": lock["patches"].pop()
    elif problem == "foreign_patch": lock["patches"].append({"file":"foreign.patch", "sha256":"0"*64})
    elif problem == "extra_field": lock["unreviewed_extension"] = True
    elif problem == "profile": lock["release_environment"]["CARGO_PROFILE_RELEASE_LTO"] = "true"
    if problem:
        with pytest.raises(ValueError, match="reviewed P17 lock"):
            pack.single_graph(lock, ROOT/"native")
    else:
        assert pack.single_graph(lock, ROOT/"native")["producer_id"] == "O165"
    assert pack.sha(old_path) == pack.P16_LOCK_SHA256


@pytest.mark.parametrize("problem", ["different_raw_bytes", "unreviewed_filename"])
def test_p17_equal_json_cannot_replace_exact_reviewed_disk_pin(tmp_path, problem):
    lock = pack.load(ROOT/"native/build-lock.O165-P17-source.json")
    native = tmp_path/"native"; native.mkdir()
    name = "build-lock.O165-P17-source.json" if problem == "different_raw_bytes" else "unreviewed.json"
    # Same parsed recipe is insufficient: both exact file identity and SHA are required.
    (native/name).write_text(json.dumps(lock, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed P17 lock"):
        pack.single_graph(lock, native)


@pytest.mark.parametrize("problem", [None, "source", "missing_fixture_patch", "foreign_field", "profile"])
def test_p18_source_graph_keeps_exact_fixture_delta_and_prior_source_identities(problem):
    old = ROOT/"native/build-lock.O165-P17-source.json"
    assert pack.sha(old) == pack.P17_LOCK_SHA256
    path = ROOT/"native/build-lock.O165-P18-source.json"
    assert pack.sha(path) == pack.P18_LOCK_SHA256
    lock = pack.load(path)
    assert lock["patches"][:-1] == pack.load(old)["patches"]
    assert lock["patches"][-1]["file"] == "cadsg-native-conditional-fixture-inheritance-0165.patch"
    if problem == "source": lock["prepared_source_sha256"] = "0"*64
    elif problem == "missing_fixture_patch": lock["patches"].pop()
    elif problem == "foreign_field": lock["unreviewed_extension"] = True
    elif problem == "profile": lock["release_environment"]["CARGO_PROFILE_RELEASE_LTO"] = "true"
    if problem:
        with pytest.raises(ValueError, match="reviewed P18 lock"):
            pack.single_graph(lock, ROOT/"native")
    else:
        assert pack.single_graph(lock, ROOT/"native")["producer_id"] == "O165"
    assert pack.sha(old) == pack.P17_LOCK_SHA256


@pytest.mark.parametrize("problem", [None, "source", "missing_trace", "trace_sha", "profile", "extra_field"])
def test_p19_source_graph_requires_exact_official_trace_delta(problem):
    previous_path = ROOT/"native/build-lock.O165-P18-source.json"
    assert pack.sha(previous_path) == pack.P18_LOCK_SHA256
    path = ROOT/"native/build-lock.O165-P19-source.json"
    assert pack.sha(path) == pack.P19_LOCK_SHA256
    lock = pack.load(path)
    assert lock["patches"][:-1] == pack.load(previous_path)["patches"]
    assert lock["patches"][-1]["file"] == "cadsg-official-local-compaction-trace-context-0165.patch"
    if problem == "source": lock["prepared_source_sha256"] = "0"*64
    elif problem == "missing_trace": lock["patches"].pop()
    elif problem == "trace_sha": lock["patches"][-1]["sha256"] = "0"*64
    elif problem == "profile": lock["release_environment"]["CARGO_PROFILE_RELEASE_LTO"] = "true"
    elif problem == "extra_field": lock["unreviewed_extension"] = True
    if problem:
        with pytest.raises(ValueError, match="reviewed P19 lock"):
            pack.single_graph(lock, ROOT/"native")
    else:
        assert pack.single_graph(lock, ROOT/"native")["producer_id"] == "O165"
    assert pack.sha(previous_path) == pack.P18_LOCK_SHA256


@pytest.mark.parametrize("problem", ["different_bytes", "different_name"])
def test_p19_equal_json_cannot_replace_exact_original_disk_pin(tmp_path, problem):
    lock = pack.load(ROOT/"native/build-lock.O165-P19-source.json")
    native = tmp_path/"native"; native.mkdir()
    name = "build-lock.O165-P19-source.json" if problem == "different_bytes" else "unreviewed.json"
    (native/name).write_text(json.dumps(lock, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed P19 lock"):
        pack.single_graph(lock, native)


@pytest.mark.parametrize("problem", [None, "source", "missing_migration", "migration_sha", "missing_helper", "helper_sha", "profile", "extra_field"])
def test_p20_source_graph_requires_exact_migration_lock_and_preserves_p19(problem):
    previous_path = ROOT/"native/build-lock.O165-P19-source.json"
    assert pack.sha(previous_path) == pack.P19_LOCK_SHA256
    path = ROOT/"native/build-lock.O165-P20-source.json"
    assert pack.sha(path) == pack.P20_LOCK_SHA256
    lock = pack.load(path)
    assert lock["patches"][:-2] == pack.load(previous_path)["patches"]
    assert lock["patches"][-2]["file"] == "cadsg-native-loopback-reconciliation-0165.patch"
    assert lock["patches"][-1]["file"] == "cadsg-native-helper-cache-bytes-0165.patch"
    if problem == "source": lock["prepared_source_sha256"] = "0"*64
    elif problem == "missing_migration": lock["patches"].pop(-2)
    elif problem == "migration_sha": lock["patches"][-2]["sha256"] = "0"*64
    elif problem == "missing_helper": lock["patches"].pop()
    elif problem == "helper_sha": lock["patches"][-1]["sha256"] = "0"*64
    elif problem == "profile": lock["release_environment"]["CARGO_PROFILE_RELEASE_LTO"] = "true"
    elif problem == "extra_field": lock["unreviewed_extension"] = True
    if problem:
        with pytest.raises(ValueError, match="reviewed P20 lock"):
            pack.single_graph(lock, ROOT/"native")
    else:
        assert pack.single_graph(lock, ROOT/"native")["producer_id"] == "O165"
    assert pack.sha(previous_path) == pack.P19_LOCK_SHA256


@pytest.mark.parametrize("problem", ["different_bytes", "different_name"])
def test_p20_equal_json_cannot_replace_exact_original_disk_pin(tmp_path, problem):
    lock = pack.load(ROOT/"native/build-lock.O165-P20-source.json")
    native = tmp_path/"native"; native.mkdir()
    name = "build-lock.O165-P20-source.json" if problem == "different_bytes" else "unreviewed.json"
    (native/name).write_text(json.dumps(lock, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed P20 lock"):
        pack.single_graph(lock, native)


@pytest.mark.parametrize("problem", [None, "source", "missing_git_overlay", "different_bytes", "different_name"])
def test_p21_source_graph_admits_only_exact_git_overlay_lock(tmp_path, problem):
    old = ROOT/"native/build-lock.O165-P20-source.json"
    path = ROOT/"native/build-lock.O165-P21-source.json"
    assert pack.sha(old) == pack.P20_LOCK_SHA256
    assert pack.sha(path) == pack.P21_LOCK_SHA256
    lock = pack.load(path)
    assert lock["patches"][:-1] == pack.load(old)["patches"]
    assert lock["patches"][-1]["file"] == "cadsg-native-plugin-git-atomic-0165.patch"
    native = ROOT/"native"
    if problem == "source":
        lock["prepared_source_sha256"] = "0"*64
    elif problem == "missing_git_overlay":
        lock["patches"].pop()
    elif problem in ("different_bytes", "different_name"):
        native = tmp_path/"native"
        native.mkdir()
        if problem == "different_bytes":
            (native/path.name).write_text(json.dumps(lock, separators=(",", ":")), encoding="utf-8")
        else:
            (native/"unreviewed.json").write_bytes(path.read_bytes())
    if problem:
        with pytest.raises(ValueError, match="reviewed P21 lock"):
            pack.single_graph(lock, native)
    else:
        assert pack.single_graph(lock, native)["producer_id"] == "O165"
