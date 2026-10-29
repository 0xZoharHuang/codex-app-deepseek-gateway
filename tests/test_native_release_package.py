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
    n = pack.load(ROOT / "native/build-lock.json")
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
    n = pack.load(ROOT / "native/build-lock.json")
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
