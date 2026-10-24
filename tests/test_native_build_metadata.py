# Pure build-metadata checks; no compiler, runtime or setup process is run.
from pathlib import Path
import hashlib
import importlib.util
import io
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
