"""Pinned license aliases: full bytes and provenance, never guessed path strings."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT/"native_licenses.py"
IS_CAG = not PATH.exists()
if IS_CAG:
    PATH = ROOT/"scripts/package_native_release.py"
spec = importlib.util.spec_from_file_location("native_notice_alias_test", PATH)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
FULL = b"Original authors and full licence conditions.\n" * 401


def git(root, *args, input=None):
    command = [os.environ.get("CODEX_DEEPSEEK_TEST_GIT", "git"), "-C", str(root),
               "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
               "-c", "user.name=fixture", "-c", "user.email=fixture@localhost",
               *args]
    return subprocess.run(command, input=input, check=True, capture_output=True).stdout


def pinned(tmp_path, *, symlinks=False, aliases=None):
    repo = tmp_path/"upstream"; repo.mkdir()
    source = repo/"crate"; source.mkdir()
    (source/"Cargo.toml").write_bytes(b'[package]\nname="fixture"\nversion="1.0.0"\n')
    (repo/"LICENSE").write_bytes(FULL)
    (repo/"licenses").mkdir()
    (repo/"licenses/NOTICE").write_bytes(b"DO_NOT_TRAVERSE_DIRECTORY_ALIAS")
    (tmp_path/"OUTSIDE_LICENSE").write_bytes(b"OUTSIDE_SOURCE_MARKER")
    aliases = aliases or {"crate/LICENSE": "../LICENSE"}
    git(repo, "init", "-q")
    git(repo, "config", "core.symlinks", "false")
    for name, target in aliases.items():
        path = repo/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(target.encode())
    git(repo, "add", "--all")
    for name, target in aliases.items():
        blob = git(repo, "hash-object", "-w", "--stdin", input=target.encode()).strip().decode()
        git(repo, "update-index", "--add", "--cacheinfo", "120000", blob, name)
    git(repo, "commit", "-q", "-m", "fixed source fixture")
    revision = git(repo, "rev-parse", "HEAD").strip().decode()
    checkout = tmp_path/"checkout"
    subprocess.run([os.environ.get("CODEX_DEEPSEEK_TEST_GIT", "git"),
                    "-c", "core.symlinks="+str(symlinks).lower(),
                    "clone", "-q", "--no-hardlinks", str(repo), str(checkout)],
                   check=True, capture_output=True)
    package = {"id": "git+https://example.invalid/fixture#fixture@1.0.0",
               "name": "fixture", "version": "1.0.0",
               "source": "git+https://example.invalid/fixture?rev="+revision+"#"+revision,
               "manifest_path": str(checkout/"crate/Cargo.toml"), "license": "MIT",
               "authors": ["original fixture author"]}
    return checkout, package


def distributed(tmp_path, package, allowed_root):
    output = tmp_path/"distributed"
    if IS_CAG:
        collector.collect_licenses({"packages": [package]}, output, [allowed_root])
    else:
        collector.collect(None, output, metadata={"packages": [package]},
                          target="x86_64-pc-windows-msvc")
    item = json.loads((output/"index.json").read_bytes())["packages"][0]
    notices = item["upstream_notice_files"] if IS_CAG else item["upstream_notice_sources"]
    row = next(row for row in notices if row["source_file"] == "LICENSE")
    file = row["file"] if IS_CAG else row["distributed_file"]
    return output/file, row


@pytest.mark.parametrize("symlinks", [False, True], ids=["Git-materialized", "real-file-symlink"])
def test_git_roundtrip_notice_alias_publishes_entire_target_and_origin(tmp_path, symlinks):
    checkout, package = pinned(tmp_path, symlinks=symlinks)
    logical = checkout/"crate/LICENSE"
    assert logical.is_symlink() is symlinks
    target, row = distributed(tmp_path, package, checkout)
    assert target.read_bytes() == FULL
    assert row["size"] == len(FULL) and row["sha256"] == hashlib.sha256(FULL).hexdigest()
    origin = row["source_resolution"]
    assert origin["kind"] == "pinned_git_checkout" and origin["resolved_source_file"] == "LICENSE"
    assert origin["git_revision"] == package["source"].rsplit("#", 1)[1]
    assert origin["alias_chain"] == [{
        "source": "crate/LICENSE", "target": "../LICENSE",
        "representation": "symlink" if symlinks else "git_materialized",
        "git_mode": "120000", "git_blob": git(checkout, "rev-parse", "HEAD:crate/LICENSE").strip().decode(),
    }]
    assert logical.is_symlink() is symlinks
    assert (checkout/"LICENSE").read_bytes() == FULL


@pytest.mark.parametrize("bad", ["external", "absolute", "cycle", "directory", "vendor",
                                 "wrong-HEAD", "wrong-index", "changed-linktext", "changed-target"])
def test_pinned_alias_boundaries_fail_closed_without_publishing_other_content(tmp_path, bad):
    aliases = {"crate/LICENSE": "../LICENSE"}
    if bad == "external": aliases = {"crate/LICENSE": "../../OUTSIDE_LICENSE"}
    elif bad == "absolute": aliases = {"crate/LICENSE": str(tmp_path/"OUTSIDE_LICENSE")}
    elif bad == "cycle": aliases = {"crate/LICENSE": "LICENSE2", "crate/LICENSE2": "LICENSE"}
    elif bad == "directory": aliases = {"crate/LICENSE": "../licenses"}
    elif bad == "vendor": aliases = {"crate/LICENSE": "../LICENSE", "crate/vendor": "../licenses"}
    checkout, package = pinned(tmp_path, aliases=aliases)
    if bad == "wrong-HEAD": package["source"] = package["source"].rsplit("#", 1)[0]+"#"+"f"*40
    elif bad == "wrong-index":
        blob = git(checkout, "rev-parse", "HEAD:crate/LICENSE").strip().decode()
        git(checkout, "update-index", "--cacheinfo", "100644", blob, "crate/LICENSE")
    elif bad == "changed-linktext": (checkout/"crate/LICENSE").write_bytes(b"../OTHER_NOTICE")
    elif bad == "changed-target": (checkout/"LICENSE").write_bytes(b"NOT_THE_PINNED_LICENSE")
    with pytest.raises(ValueError):
        distributed(tmp_path, package, checkout)
    output = tmp_path/"distributed"
    if output.exists():
        assert not any(b"OUTSIDE_SOURCE_MARKER" in p.read_bytes()
                       for p in output.rglob("*") if p.is_file())
    assert (tmp_path/"OUTSIDE_LICENSE").read_bytes() == b"OUTSIDE_SOURCE_MARKER"


def test_nonalias_short_text_is_not_interpreted_as_a_path(tmp_path):
    source = tmp_path/"crate"; source.mkdir()
    (source/"LICENSE").write_bytes(b"../OUTSIDE_LICENSE")
    (tmp_path/"OUTSIDE_LICENSE").write_bytes(b"OUTSIDE_SOURCE_MARKER")
    content, origin = collector.notice_content(source/"LICENSE", source)
    assert content == b"../OUTSIDE_LICENSE" and origin is None


def test_registry_internal_notice_alias_and_declared_custom_alias(tmp_path):
    source = tmp_path/"crate"; source.mkdir()
    (source/"COPYING").write_bytes(FULL)
    (source/"LICENSE").symlink_to("COPYING")
    content, origin = collector.notice_content(source/"LICENSE", source)
    assert content == FULL and origin["resolved_source_file"] == "COPYING"
    (source/"Cargo.toml").write_bytes(b"[package]")
    (source/"legal").mkdir(); (source/"legal/terms.txt").symlink_to("../COPYING")
    package = {"manifest_path": str(source/"Cargo.toml"), "source": "registry+fixture",
               "license_file": "legal/terms.txt"}
    context = collector.source_context(package)
    assert source/"legal/terms.txt" in collector.notice_candidates(source, context)
    assert collector.notice_content(source/"legal/terms.txt", source, context)[0] == FULL


def test_shared_git_scope_cannot_bypass_explicit_assembler_allowed_roots(tmp_path):
    checkout, package = pinned(tmp_path)
    context = collector.source_context(package); source = checkout/"crate"
    context["allowed_roots"] = [source]
    with pytest.raises(ValueError, match="declared source/cache"):
        collector.notice_content(source/"LICENSE", source, context)


def test_host_case_semantics_do_not_blindly_casefold_Posix_paths(tmp_path):
    checkout, package = pinned(tmp_path)
    package["manifest_path"] = str(checkout/"CRATE"/"Cargo.toml")
    if os.name == "nt":
        context = collector.source_context(package)
        content, origin = collector.notice_content(checkout/"CRATE/LICENSE", checkout/"CRATE", context)
        assert content == FULL and origin["git_revision"] == package["source"].rsplit("#", 1)[1]
    else:
        # Even on a case-insensitive Unix volume, Git source path identity is exact.
        with pytest.raises(ValueError):
            context = collector.source_context(package)
            collector.notice_content(checkout/"CRATE/LICENSE", checkout/"CRATE", context)


def test_explicit_shared_git_license_file_is_admitted_only_inside_locked_root(tmp_path):
    checkout, package = pinned(tmp_path)
    package["license_file"] = "../LICENSE"
    source = checkout/"crate"
    context = collector.source_context(package)
    content, origin = collector.notice_content(checkout/"LICENSE", source, context)
    assert content == FULL and origin["declaration"] == "Cargo license_file"
    assert origin["resolved_source_file"] == "LICENSE"
    assert origin["alias_chain"] == []
    context["declared_notice"] = tmp_path/"OUTSIDE_LICENSE"
    with pytest.raises(ValueError, match="escapes"):
        collector.notice_content(tmp_path/"OUTSIDE_LICENSE", source, context)


def test_case_only_target_follows_host_identity_and_rejects_Windows_ambiguity(tmp_path):
    checkout, package = pinned(tmp_path, aliases={"crate/LICENSE": "../license"})
    source = checkout/"crate"
    if os.name == "nt":
        content, origin = collector.notice_content(source/"LICENSE", source, collector.source_context(package))
        assert content == FULL and origin["resolved_source_file"] == "LICENSE"
    else:
        with pytest.raises(ValueError):
            collector.notice_content(source/"LICENSE", source, collector.source_context(package))


def test_distinct_Posix_targets_are_not_silently_merged_by_Windows_fold(tmp_path):
    checkout, package = pinned(tmp_path)
    lower = b"Distinct lowercase license contents\n"
    blob = git(checkout, "hash-object", "-w", "--stdin", input=lower).strip().decode()
    git(checkout, "update-index", "--add", "--cacheinfo", "100644", blob, "license")
    git(checkout, "commit", "-q", "-m", "case-distinct source paths")
    revision = git(checkout, "rev-parse", "HEAD").strip().decode()
    package["source"] = "git+https://example.invalid/fixture?rev="+revision+"#"+revision
    distinct_files = not (checkout/"license").exists() or not (checkout/"license").samefile(checkout/"LICENSE")
    if os.name != "nt" and distinct_files:
        (checkout/"license").write_bytes(lower)
    source = checkout/"crate"
    context = collector.source_context(package)
    if os.name == "nt":
        with pytest.raises(ValueError, match="ambiguous"):
            collector.notice_content(source/"LICENSE", source, context)
    else:
        content, origin = collector.notice_content(source/"LICENSE", source, context)
        assert content == FULL and origin["resolved_source_file"] == "LICENSE"
        if distinct_files:
            assert (checkout/"license").read_bytes() == lower
        else:
            # A case-insensitive Unix volume cannot materialize both Git names.
            # The selected exact-name notice must still match its locked blob.
            assert (checkout/"LICENSE").read_bytes() == FULL


def test_explicit_Git_executable_works_without_PATH_git(tmp_path, monkeypatch):
    import shutil
    executable = os.environ.get("CODEX_DEEPSEEK_TEST_GIT") or shutil.which("git")
    assert executable and Path(executable).is_file()
    checkout, package = pinned(tmp_path)
    monkeypatch.setenv("PATH", "")
    source = checkout/"crate"
    context = collector.source_context(package, git=executable)
    content, origin = collector.notice_content(source/"LICENSE", source, context)
    assert content == FULL and origin["resolved_source_file"] == "LICENSE"
    if IS_CAG:
        output=tmp_path/"explicit-git-output"
        collector.collect_licenses({"packages":[package]},output,[checkout],git=executable)
        item=json.loads((output/"index.json").read_bytes())["packages"][0]
        notice=next(x for x in item["upstream_notice_files"]if x["source_file"]=="LICENSE")
        assert (output/notice["file"]).read_bytes()==FULL
