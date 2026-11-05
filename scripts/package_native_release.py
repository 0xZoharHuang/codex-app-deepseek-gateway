"""Assemble the raw Windows native distribution; never compile or execute it.

Notice collection copies the same pinned build-time pure helper region as CDS
native_licenses.py, under Apache-2.0; packaging provenance records source hashes. SPDX data retain their own exact upstream commit and hashes.
No CDS runtime, launcher, supervisor or tool execution module is imported.
"""
from __future__ import annotations
import argparse
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile

REPO = Path(__file__).resolve().parents[1]
BINARIES = frozenset({"codex.exe", "codex-command-runner.exe",
                     "codex-windows-sandbox-setup.exe", "codex-windows-managed-deny-probe.exe"})
TARGET = "x86_64-pc-windows-msvc"
SOURCE_BUNDLE_KIND = "cadsg-native-source-bundle"
SOURCE_BINARIES = BINARIES | {"codex-windows-sandbox-service.exe", "codex-code-mode-host.exe"}
P16_LOCK_SHA256 = "c294196f4b56f63578fb81a4d09b3830b0b7e14600fa2b0c4688893fb3db002b"
P17_LOCK_SHA256 = "3a57c36fab0ea6c25b8f836702350b20eab93b1369e02e20dcf9e3a32dc7681f"
P18_LOCK_SHA256 = "16033bf6d16dedf440b3d9355445a87f8098f340958c2cd7685028330a5281cb"
P19_LOCK_SHA256 = "163f6e61d605adf7d7698835f33b99d48c5cb25f017b8231f5062bd0ffb0a543"
P20_LOCK_SHA256 = "3736e573e634180a1443b0c3e2cdbbf3d28304cf17d76fd3752aab344356b44c"
REVIEWED_SOURCE_LOCKS = (("build-lock.O165-P16-source.json", P16_LOCK_SHA256),
                         ("build-lock.O165-P17-source.json", P17_LOCK_SHA256),
                         ("build-lock.O165-P18-source.json", P18_LOCK_SHA256),
                         ("build-lock.O165-P19-source.json", P19_LOCK_SHA256),
                         ("build-lock.O165-P20-source.json", P20_LOCK_SHA256))


def binary_names(manifest):
    if manifest.get("kind") == SOURCE_BUNDLE_KIND:
        require(manifest.get("runtime_mode") == "legacy", "Source bundle must explicitly use native Legacy mode")
        return SOURCE_BINARIES
    require("kind" not in manifest, "Unknown raw native bundle kind")
    return BINARIES


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode() + b"\n")


def redirected(path, info=None):
    # FILE_ATTRIBUTE_REPARSE_POINT covers junctions and other Windows tags,
    # including Python 3.11 where is_junction() is not available.
    info = Path(path).lstat() if info is None else info
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def plain(path):
    path = Path(path)
    require(not any(redirected(p) for p in (path, *path.parents)), "Input path is redirected")
    require(path.is_file(), "Input file is missing")
    return path


def copy_bytes(source, destination, expected=None):
    source = plain(source); destination = Path(destination)
    before = source.stat(); digest = expected or sha(source)
    require(sha(source) == digest, "Input checksum differs")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    after = source.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_nlink) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_nlink),
            "Input changed during independent copy")
    require(not source.samefile(destination) and destination.stat().st_nlink == 1
            and sha(source) == digest == sha(destination), "Independent copy verification failed")
    return {"sha256": digest, "size": destination.stat().st_size}


def raw_outputs(manifest, *, expected=None):
    require(manifest.get("schema_version") == 1 and
            manifest.get("status") in {"passed", "built-not-app-qualified"},
            "Only an unprepared raw native manifest is distributable")
    outputs = manifest.get("outputs", manifest.get("binaries"))
    require(isinstance(outputs, list), "Raw binary list is missing")
    names = [row.get("name", row.get("file")) for row in outputs]
    expected = binary_names(manifest) if expected is None else expected
    require(len(names) == len(expected) and set(names) == expected,
            "Raw distribution must contain exactly the declared self-built native binaries")
    for row, name in zip(outputs, names):
        require("path" not in row and "source" not in row, "Raw output cannot import a prepared/App path")
        require(type(row.get("size")) is int and row["size"] > 0
                and re.fullmatch("[0-9a-f]{64}", row.get("sha256", "")), "Invalid raw binary identity")
    return dict(zip(names, outputs))


def indexed_sources(raw, index, index_digest, references_digest, n_lock, j_lock):
    """The bundle's source hash identifies a mixed-producer index, not N alone."""
    require(raw["source_sha256"] == raw["source_index"]["sha256"] == index_digest
            and index.get("kind") == "per_binary_native_source_index",
            "Raw source index identity differs")
    require(index["producer_references"]["file"] == "producer-references.json"
            and index["producer_references"]["sha256"] == references_digest,
            "Source-index producer reference differs")
    require(index["producer_sources"]["N-CLI"]["source_sha256"] == n_lock["prepared_source_sha256"]
            and index["producer_sources"]["J17"]["source_sha256"] == j_lock["prepared_source_sha256"],
            "Indexed per-binary source producers differ")
    fields = ("name", "size", "sha256", "producer_id")
    expected = sorted(({k:row[k] for k in fields} for row in raw["outputs"]), key=lambda r:r["name"])
    actual = sorted(({k:row[k] for k in fields} for row in index["binary_producers"]), key=lambda r:r["name"])
    require(actual == expected and all(row["producer_id"] == ("N-CLI" if row["name"] == "codex.exe" else "J17")
                                      for row in actual), "Indexed binary producers differ")


def single_graph(lock, native_dir):
    """One source/recipe produces all four current binaries; no N/J inference."""
    require(lock["candidate_revision"] == "O165"
            and lock["upstream_commit"] == "720fec862a608ae1e5878d1eecb04fb93192e93c"
            and lock["native_version"] == "0.165.0-alpha.1.2"
            and lock["target"] == TARGET, "Unsupported single-producer baseline")
    names = set(lock["binaries"])
    require(len(lock["binaries"]) == len(names) and names in (BINARIES, SOURCE_BINARIES),
            "Single producer must build the exact four or six native targets")
    source_bundle = names == SOURCE_BINARIES
    if source_bundle:
        reviewed = []
        for name, digest in REVIEWED_SOURCE_LOCKS:
            candidate = native_dir / name
            if candidate.exists():
                pinned = plain(candidate)
                if sha(pinned) == digest and load(pinned) == lock:
                    reviewed.append(name)
        require(len(reviewed) == 1,
                "Six-target source bundle must match an explicitly reviewed P16 lock or reviewed P17 lock or reviewed P18 lock or reviewed P19 lock or reviewed P20 lock")
    base_patches = [
        "windows-home-namespace-0165.patch", "mcp-keyring-isolation-0165.patch",
        "guardian-thread-context-0165.patch", "windows-cwd-ancestor-metadata-0165.patch"]
    tectonic_patches = base_patches + ["packaged-tectonic-materialization-0165.patch"]
    guardian_transport_patches = tectonic_patches + ["guardian-provider-transport-0165.patch"]
    migration_eol_patches = guardian_transport_patches + ["migration-checksum-eol-0165.patch"]
    require(source_bundle or [row["file"] for row in lock["patches"]] in (
        base_patches, tectonic_patches, guardian_transport_patches, migration_eol_patches),
        "Single-producer patch topology differs")
    for row in lock["patches"]:
        require(sha(plain(native_dir / row["file"])) == row["sha256"],
                "Single-producer native patch checksum differs")
    return {"method":"One exact source fingerprint, Cargo.lock, compiler/profile and locked native target invocation.",
            "Cargo_lock_sha256":lock["cargo_lock_sha256"],
            "producer_sources_remain_separate":False, "producer_id":"O165"}


def single_sources(raw, lock, lock_digest, raw_digest, build):
    """Validate the builder receipt and project only public, relative identities."""
    outputs = raw_outputs(raw, expected=frozenset(lock["binaries"]))
    require(raw["candidate_revision"] == lock["candidate_revision"]
            and raw["build_lock_sha256"] == lock_digest
            and raw["upstream_commit"] == lock["upstream_commit"]
            and raw["native_version"] == lock["native_version"]
            and raw["archive_sha256"] == lock["archive_sha256"]
            and raw["source_sha256"] == lock["prepared_source_sha256"]
            and raw["patches"] == lock["patches"]
            and raw["profile"] == lock["release_environment"] == build.PROFILE,
            "Single-producer raw inputs differ from pinned recipe")
    resource_keys = ("locked_max_build_jobs", "effective_build_jobs", "build_jobs_override")
    resource_policy = None
    override = None
    if any(key in raw for key in resource_keys):
        override = raw.get("build_jobs_override")
        resource_policy = build.build_jobs_policy(override)
        require(all(key in raw and type(raw[key]) is type(value) and raw[key] == value
                    for key, value in resource_policy.items()),
                "Single-producer build jobs metadata differs")
    expected, = build.build_commands(Path("pinned-rust"), lock["binaries"], jobs=override,
                                              runtime_package=set(lock["binaries"]) == SOURCE_BINARIES)
    cache = Path(raw.get("cache_dir", raw["work_dir"]))
    require(len(raw["commands"]) == 1 and raw["commands"][0][1:] == expected[1:]
            and raw["cargo_home"] == str(cache / "cargo"),
            "Single-producer build invocation differs")
    if raw["target"] != str(cache / "target"):
        # New builder receipts record the effective independent target directly.
        # Reuse its exact path policy; never infer a cache alias or rewrite a receipt.
        require(all(isinstance(raw.get(key), str) and raw[key]
                    and Path(raw[key]).is_absolute() for key in ("target", "source", "output")),
                "Independent target requires absolute builder target/source/output")
        target = Path(raw["target"])
        require(build.target_directory(cache, Path(raw["source"]), Path(raw["output"]), target) == target,
                "Independent target differs from the standard builder path policy")
    rows = [{"name":name, "size":row["size"], "sha256":row["sha256"], "producer_id":"O165"}
            for name,row in sorted(outputs.items())]
    refs = {"schema_version":1, "kind":"single_source_native_producer",
            "original_raw_manifest_sha256":raw_digest,
            "producer_id":"O165", "build_lock_sha256":lock_digest,
            "source_sha256":lock["prepared_source_sha256"], "source_files":lock["prepared_source_files"],
            "upstream_commit":lock["upstream_commit"], "archive_sha256":lock["archive_sha256"],
            "cargo_lock_sha256":lock["cargo_lock_sha256"], "target":lock["target"],
            "rust_version":lock["rust_version"], "release_environment":lock["release_environment"],
            "binaries":rows}
    index = {"schema_version":1, "kind":"single_source_native_index",
             "source_sha256":lock["prepared_source_sha256"], "producer_id":"O165", "binary_producers":rows}
    public = {"schema_version":1, "status":"built-not-app-qualified",
              "native_version":lock["native_version"], "upstream_commit":lock["upstream_commit"],
              "source_sha256":lock["prepared_source_sha256"], "build_lock_sha256":lock_digest,
              "candidate_revision":"O165", "outputs":rows}
    if set(lock["binaries"]) == SOURCE_BINARIES:
        public.update(kind=SOURCE_BUNDLE_KIND, runtime_mode="legacy")
        refs["runtime_layout"] = "unpackaged native Legacy; no OS package identity asserted"
    if resource_policy is not None:
        refs["build_resources"] = dict(resource_policy)
        public["build_resources"] = dict(resource_policy)
    return refs, index, public


def source_index_location(raw_path, raw):
    relative = PurePosixPath(raw["source_index"]["file"])
    require(not relative.is_absolute() and ".." not in relative.parts
            and "\\" not in str(relative) and ":" not in str(relative)
            and relative.name == "native-source-index.json",
            "Unsafe source-index location")
    path = plain(Path(raw_path).parent.joinpath(*relative.parts))
    require(path.resolve().is_relative_to(Path(raw_path).parent.resolve()), "Source index escapes raw input")
    return path

def shared_graph(n_lock, j_lock, native_dir):
    require(n_lock["target"] == j_lock["target"] == TARGET, "Native targets differ")
    for name in ("upstream_commit", "archive_sha256", "native_version", "rust_version", "cargo_lock_sha256"):
        require(n_lock[name] == j_lock[name], "N/J dependency input differs: " + name)
    require(n_lock["patches"][:len(j_lock["patches"])] == j_lock["patches"],
            "Retained J patch prefix differs")
    extra = n_lock["patches"][len(j_lock["patches"]):]
    require(extra, "No separately attested N overlay")
    allowed = {"codex-rs/windows-sandbox-rs/src/setup.rs",
               "codex-rs/windows-sandbox-rs/src/setup_refresh_launch.rs",
               "codex-rs/windows-sandbox-rs/src/setup_refresh_launch_tests.rs",
               "codex-rs/core/src/session/request_capacity.rs"}
    touched = []
    for row in n_lock["patches"]:
        require(sha(plain(native_dir / row["file"])) == row["sha256"], "Native patch checksum differs")
    for row in extra:
        lines = (native_dir / row["file"]).read_text(encoding="utf-8").splitlines()
        paths = [line[6:] for line in lines if line.startswith("+++ b/")]
        require(paths and all(path in allowed for path in paths),
                "Additional N patch changes unreviewed dependency/build inputs")
        touched.extend(paths)
    return {"method": "Identical fixed upstream + exact J patch prefix; N-only overlays touch only the listed Rust files, not any Cargo manifest/config/lock. Thus the same target workspace dependency graph is a shared external-dependency superset.",
            "Cargo_lock_sha256": n_lock["cargo_lock_sha256"], "N_only_changed_paths": sorted(set(touched)),
            "producer_sources_remain_separate": True}


# BEGIN PINNED BUILD-TIME NOTICE HELPERS
def _redirected(path):
    path = Path(path)
    return path.is_symlink() or bool(
        getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400)


# Cargo crates may vendor notices below arbitrary paths. Directory walks never
# follow redirects; only notice-file aliases are resolved, inside an admitted
# crate or the exact Cargo-pinned Git checkout that owns that crate.
NOTICE_TOKENS = ("LICENSE", "NOTICE", "COPYING", "COPYRIGHT")
NESTED_NOTICE_NAME = re.compile(r"^(?:licen[cs]e|notice|copying|copyright)(?:[._-].*)?$", re.IGNORECASE)
NOTICE_FOLDERS = (("licenses",), ("LICENSES",), ("third_party", "licenses"), ("third-party", "licenses"))
MAX_NOTICE_SCAN_ENTRIES = 100_000
MAX_NOTICE_SCAN_DEPTH = 32
MAX_CRATE_NOTICE_FILES = 4_096
MAX_CRATE_NOTICE_BYTES = 64 * 1024 * 1024
MAX_NOTICE_ALIAS_HOPS = 16


def _git(root, *arguments, executable="git"):
    try:
        result = subprocess.run([executable, "-C", str(root), *arguments],
                                check=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        raise ValueError("Git notice identity command failed at " + str(root) + ": " + " ".join(arguments)) from error
    return result.stdout


def _plain_parents(path):
    require(not any(_redirected(p) for p in Path(path).parents),
            "Crate notice parent is redirected: " + str(path))


def _notice_name(path, source):
    relative = path.relative_to(source)
    return (len(relative.parts) == 1 and any(token in path.name.upper() for token in NOTICE_TOKENS)
            or any(relative.parts[:len(prefix)] == prefix for prefix in NOTICE_FOLDERS)
            or bool(NESTED_NOTICE_NAME.fullmatch(path.name)))


def source_context(package, *, git="git"):
    """Cargo Git identity admits a shared root; arbitrary parent directories do not."""
    source = Path(os.path.abspath(package["manifest_path"])).parent
    require(source.is_dir(), "Crate source directory is missing: " + str(source))
    require(not any(_redirected(p) for p in (source, *source.parents)),
            "Crate source path is redirected: " + str(source))
    context = {"source": source, "root": source, "kind": "crate", "tree": {}, "index": {}, "git_executable": git}
    if package.get("license_file"):
        declared_path = Path(package["license_file"])
        context["declared_notice"] = Path(os.path.abspath(declared_path if declared_path.is_absolute() else source / declared_path))
    declared = package.get("source", "")
    if not declared.startswith("git+"):
        return context
    match = re.fullmatch(r"git\+[^#\x00\r\n]+#([0-9a-f]{40})", declared)
    require(match is not None, "Cargo Git source needs an exact full revision: " + declared)
    revision = match[1]
    query_revision = re.search(r"[?&]rev=([0-9a-f]{40})(?:[&#]|$)", declared)
    require(query_revision is None or query_revision[1] == revision,
            "Cargo Git source revision query differs from its locked commit: " + declared)
    root = Path(os.path.abspath(_git(source, "rev-parse", "--show-toplevel", executable=git).decode().strip()))
    require(source.is_relative_to(root), "Crate is outside its Git checkout: " + str(source))
    require(not any(_redirected(p) for p in (root, *root.parents)),
            "Git source root is redirected: " + str(root))
    require(_git(root, "rev-parse", "HEAD", executable=git).decode().strip() == revision,
            "Git checkout HEAD differs from Cargo source revision: " + str(source))
    tree_raw = _git(root, "ls-tree", "-r", "-z", revision, executable=git)
    index_raw = _git(root, "ls-files", "--stage", "-z", executable=git)
    require(len(tree_raw) <= 16 * 1024 * 1024 and len(index_raw) <= 16 * 1024 * 1024,
            "Git notice inventory exceeds bounded bytes")
    tree, index = {}, {}
    for raw, destination, is_index in [(tree_raw, tree, False), (index_raw, index, True)]:
        entries = raw.rstrip(b"\0").split(b"\0") if raw else []
        require(len(entries) <= MAX_NOTICE_SCAN_ENTRIES, "Git notice inventory exceeds bounded entries")
        for entry in entries:
            prefix, filename = entry.split(b"\t", 1)
            fields = prefix.split()
            name = filename.decode("utf-8")
            require("\\" not in name and not Path(name).is_absolute()
                    and ".." not in Path(name).parts, "Unsafe Git notice path: " + name)
            if is_index:
                require(fields[2] == b"0", "Git notice index contains unresolved stages: " + name)
                value = (fields[0].decode(), fields[1].decode())
            else:
                value = (fields[0].decode(), fields[2].decode())
            require(name not in destination, "Duplicate Git notice path: " + name)
            destination[name] = value
    folded = {}
    for name in tree:
        folded.setdefault(name.casefold(), []).append(name)
    context.update(root=root, kind="pinned_git_checkout", revision=revision,
                   declared=declared, tree=tree, index=index, folded=folded)
    return context


def _git_entry(context, path):
    relative = path.relative_to(context["root"]).as_posix()
    if os.name == "nt":
        matches = context["folded"].get(relative.casefold(), [])
        require(len(matches) == 1, "Missing or ambiguous Windows Git notice path: " + relative)
        relative = matches[0]
    require(relative in context["tree"], "Notice is not tracked at the Cargo revision: " + relative)
    value = context["tree"][relative]
    require(context["index"].get(relative) == value,
            "Git notice index differs from the Cargo revision: " + relative)
    return relative, value


def _resolve_notice(path, source, context):
    path = Path(os.path.abspath(path))
    shared_declaration = (context["kind"] == "pinned_git_checkout"
                          and path == context.get("declared_notice")
                          and path.is_relative_to(context["root"]))
    require(path.is_relative_to(source) or shared_declaration,
            "Crate notice escapes its source: " + str(path))
    root, seen, chain = context["root"], set(), []
    current = path
    for _ in range(MAX_NOTICE_ALIAS_HOPS + 1):
        require(current.is_relative_to(root), "Crate notice alias escapes its source scope: " + str(current))
        if context.get("allowed_roots"):
            require(any(current.is_relative_to(admitted) for admitted in context["allowed_roots"]),
                    "Crate notice escapes declared source/cache: " + str(current))
        require(current not in seen, "Crate notice alias cycle: " + str(current))
        seen.add(current); _plain_parents(current)
        relative = current.relative_to(root).as_posix()
        git_name, entry = _git_entry(context, current) if context["kind"] == "pinned_git_checkout" else (relative, None)
        actual_link = current.is_symlink()
        git_link = entry is not None and entry[0] == "120000"
        info = current.lstat()
        require(not _redirected(current) or actual_link,
                "Crate notice contains a redirected non-symlink entry: " + str(current))
        if not actual_link and not git_link:
            require(stat.S_ISREG(info.st_mode), "Crate notice is not a regular file: " + str(current))
            if entry is not None:
                require(entry[0] in {"100644", "100755"}, "Invalid Git notice file mode: " + git_name)
            return current, chain, (git_name, entry)
        require(len(chain) < MAX_NOTICE_ALIAS_HOPS, "Crate notice alias chain exceeds bounded hops")
        if git_link:
            target_bytes = _git(root, "cat-file", "blob", entry[1], executable=context["git_executable"])
            require(len(target_bytes) <= 4096, "Git notice alias target exceeds bounded bytes")
            target = target_bytes.decode("utf-8")
            if actual_link:
                require(current.readlink() == Path(target),
                        "Git notice symlink differs from the pinned target: " + str(current))
            else:
                require(stat.S_ISREG(info.st_mode) and current.read_bytes() == target_bytes,
                        "Materialized Git notice alias differs from its pinned blob: " + str(current))
        else:
            require(context["kind"] == "crate", "Untracked Git notice alias: " + str(current))
            target = str(current.readlink())
        target_path = Path(target)
        require(target and not target_path.is_absolute() and not target_path.drive
                and not any(ch in target for ch in "\0\r\n"),
                "Crate notice alias target must be a relative file path: " + str(current))
        chain.append({"source": git_name, "target": target,
                      "representation": "symlink" if actual_link else "git_materialized",
                      **({"git_mode": "120000", "git_blob": entry[1]} if git_link else {})})
        current = Path(os.path.abspath(current.parent / target_path))
    raise ValueError("Crate notice alias chain exceeds bounded hops")


def notice_candidates(source, context=None):
    """No-follow walk; validate aliases before admitting their logical notice path."""
    source = Path(os.path.abspath(source))
    context = context or {"source": source, "root": source, "kind": "crate", "tree": {}, "index": {}}
    require(source.is_dir(), "Crate source directory is missing: " + str(source))
    require(not any(_redirected(p) for p in (source, *source.parents)),
            "Crate source path is redirected: " + str(source))
    pending, candidates, visited = [source], set(), 0
    while pending:
        folder = pending.pop()
        require(len(folder.relative_to(source).parts) <= MAX_NOTICE_SCAN_DEPTH,
                "Crate notice scan exceeds the bounded directory depth")
        with os.scandir(folder) as entries:
            for entry in entries:
                visited += 1
                require(visited <= MAX_NOTICE_SCAN_ENTRIES, "Crate notice scan exceeds the bounded entry count")
                path = Path(entry.path)
                require(path.is_relative_to(source), "Crate notice scan escapes its source: " + str(path))
                logical_notice = _notice_name(path, source) or path == context.get("declared_notice")
                git_alias = False
                if context["kind"] == "pinned_git_checkout":
                    relative = path.relative_to(context["root"]).as_posix()
                    matches = context["folded"].get(relative.casefold(), []) if os.name == "nt" else [relative]
                    git_alias = any(context["tree"].get(name, ("", ""))[0] == "120000" for name in matches)
                if entry.is_symlink() or _redirected(path) or git_alias:
                    require(logical_notice, "Crate notice scan contains a redirected entry: " + str(path))
                    _resolve_notice(path, source, context)
                    candidates.add(path)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                else:
                    require(entry.is_file(follow_symlinks=False),
                            "Crate notice scan contains a non-regular entry: " + str(path))
                    if logical_notice:
                        candidates.add(path)
                require(len(candidates) <= MAX_CRATE_NOTICE_FILES,
                        "Crate notice count exceeds the bounded file count")
    return candidates


def _regular_notice_bytes(path, scope):
    require(not any(_redirected(p) for p in (path, *path.parents)),
            "Crate notice path is redirected: " + str(path))
    require(path.is_relative_to(scope), "Crate notice escapes its source: " + str(path))
    before = path.stat()
    require(stat.S_ISREG(before.st_mode), "Crate notice is not a regular file: " + str(path))
    require(before.st_size <= 16 * 1024 * 1024, "Crate notice exceeds the bounded file size")
    with path.open("rb") as stream:
        content = stream.read(16 * 1024 * 1024 + 1)
        opened = os.fstat(stream.fileno())
    after = path.stat()
    fields = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink)
    require(fields(before) == fields(opened) == fields(after) and len(content) == before.st_size,
            "Crate notice changed during copy: " + str(path))
    require(content[:2] != b"MZ" and content[:4] not in (b"\x7fELF", b"PK\x03\x04"),
            "Compiled/container bytes are not a native source notice")
    return content


def notice_content(path, source, context=None):
    source = Path(os.path.abspath(source))
    context = context or {"source": source, "root": source, "kind": "crate", "tree": {}, "index": {}}
    resolved, chain, git_entry = _resolve_notice(path, source, context)
    content = _regular_notice_bytes(resolved, context["root"])
    if context["kind"] == "pinned_git_checkout":
        require(content == _git(context["root"], "cat-file", "blob", git_entry[1][1], executable=context["git_executable"]),
                "Git notice content differs from the Cargo revision: " + str(resolved))
    require((resolved, chain, git_entry) == _resolve_notice(path, source, context),
            "Crate notice alias changed during copy: " + str(path))
    provenance = None
    shared_declaration = not Path(os.path.abspath(path)).is_relative_to(source)
    if chain or shared_declaration:
        provenance = {"kind": context["kind"], "resolved_source_file": git_entry[0],
                      "alias_chain": chain}
        if shared_declaration:
            provenance["declaration"] = "Cargo license_file"
        if context["kind"] == "pinned_git_checkout":
            provenance.update(git_source=context["declared"], git_revision=context["revision"])
    return content, provenance


def _notice_bytes(path, source, context=None):
    return notice_content(path, source, context)[0]
# END PINNED BUILD-TIME NOTICE HELPERS

def collect_licenses(metadata, output, allowed_roots, *, repo=REPO, git="git"):
    """Retain original bytes and attribution; reject missing/unknown declarations."""
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    roots = [Path(p).resolve() for p in allowed_roots]
    def admitted(path):
        path = plain(Path(path))
        require(any(path.resolve().is_relative_to(root) for root in roots), "Crate notice escapes declared source/cache")
        require(path.stat().st_size <= 16 * 1024 * 1024, "Crate notice exceeds the bounded file size")
        return path
    data = load(repo / "native/license-data.json")
    ids = {row["id"] for row in data["texts"]}
    for row in data["texts"]:
        path = plain(repo / "native/licenses/spdx" / (row["id"] + ".txt"))
        copy_bytes(path, output / "spdx" / path.name, row["sha256"])
    entries = []
    for package in sorted(metadata["packages"], key=lambda p: p["id"]):
        if not package.get("source"):
            continue
        manifest = admitted(package["manifest_path"]); source = manifest.parent
        context = source_context(package, git=git)
        context["allowed_roots"] = roots
        expression = package.get("license")
        require(isinstance(expression, str) and expression, "External crate has no declared license")
        symbols = set(re.findall(r"[A-Za-z0-9.+-]+", expression)) - {"AND", "OR", "WITH"}
        require(symbols <= ids, "Unpinned license identifier in crate " + package["name"])
        key = package["name"] + "-" + package["version"] + "-" + hashlib.sha256(package["id"].encode()).hexdigest()[:8]
        require(PurePosixPath(key).name == key and "\\" not in key, "Unsafe crate identity")
        target = output / "crates" / key
        require(not output.resolve().is_relative_to(source.resolve()),
                "License output cannot be inside crate source")
        try:
            candidates = notice_candidates(source, context)
        except ValueError as error:
            raise ValueError(f"Crate {package['name']}@{package['version']} root={source}: {error}") from error
        if package.get("license_file"):
            p = Path(package["license_file"])
            candidates.add(p if p.is_absolute() else source / p)
        fallback = not candidates
        if fallback:
            candidates.update(source.glob("README*"))
        require(len(candidates) <= MAX_CRATE_NOTICE_FILES,
                "Crate notice count exceeds the bounded file count")
        notices = []
        notice_bytes = 0
        for index, path in enumerate(sorted(candidates)):
            try:
                content, resolution = notice_content(path, source, context)
            except ValueError as error:
                raise ValueError(f"Crate {package['name']}@{package['version']} notice={path}: {error}") from error
            notice_bytes += len(content)
            require(notice_bytes <= MAX_CRATE_NOTICE_BYTES,
                    "Crate notices exceed the bounded aggregate size")
            name = str(index) + "-" + path.name
            dest = target / name; dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("xb") as stream:
                stream.write(content)
            digest = hashlib.sha256(content).hexdigest()
            require(dest.stat().st_nlink == 1 and sha(dest) == digest,
                    "Independent notice copy verification failed")
            notices.append({"file": dest.relative_to(output).as_posix(),
                            "source_file": Path(os.path.relpath(path, source)).as_posix(),
                            "sha256": digest, "size": len(content),
                            **({"source_resolution": resolution} if resolution else {})})
        require(notices or expression, "Crate has neither notice nor declaration")
        item = {"name": package["name"], "version": package["version"], "source": package["source"],
                "repository": package.get("repository"), "authors": package.get("authors", []),
                "declared_license": expression, "manifest_sha256": sha(manifest),
                "upstream_notice_files": notices, "README_fallback": fallback}
        write(target / "attribution.json", item); entries.append(item)
    index = {"schema_version": 1, "scope": "External dependency superset of the fixed Windows native workspace; includes build/test and unused workspace dependencies, not a claim all were linked.",
             "spdx_source": data, "packages": entries}
    write(output / "index.json", index)
    return index


def validate_tree(root, *, build_lock=None):
    root = Path(root); inventory = {}
    manifest = load(root / "manifest.json")
    names = binary_names(manifest)
    single = manifest.get("candidate_revision") == "O165"
    if build_lock is not None:
        # Honor the exact explicitly selected producer lock through staging and
        # archiving too; an unpromoted candidate must not borrow the active lock.
        require(single, "Explicit staging lock requires a single-source producer")
        selected = plain(build_lock)
        cohort = load(selected)
        single_graph(cohort, REPO / "native")
        require(manifest.get("build_lock_sha256") == sha(selected),
                "Staging manifest differs from the explicit build lock")
    else:
        cohort_lock = "build-lock.json" if single else "build-lock.N.json"
        cohort = load(REPO / "native" / cohort_lock)
    admitted_patches = {row["file"]: row["sha256"] for row in cohort["patches"]}
    for p in sorted(root.rglob("*")):
        require(not p.is_symlink() and not (hasattr(p, "is_junction") and p.is_junction()),
                "Release staging contains a redirected entry")
        if not p.is_file():
            continue
        name = p.relative_to(root).as_posix()
        metadata_names = {"producer-references.json", "original-raw-manifest.json",
                          "native-source-index.json",
                          "dependency-graph.json", "source.json", "Cargo.lock",
                          "distribution-inventory.json"}
        metadata_names.update({"O165-original-build-lock.json"} if single else {
            "N-original-build-lock.json", "J17-original-build-lock.json", "M-original-baseline-lock.json"})
        provenance = name.startswith("provenance/") and name[len("provenance/"):] in metadata_names
        patch = name.startswith("provenance/patches/") and name[len("provenance/patches/"):] in admitted_patches
        require(name == "manifest.json" or name in names or name in {"LICENSE", "NOTICE"}
                or name.startswith("licenses/") or provenance or patch, "Unexpected public native file")
        if patch:
            require(sha(p) == admitted_patches[name[len("provenance/patches/"):]],
                    "Staged native patch checksum differs from the selected build lock")
        if name not in names:
            with p.open("rb") as f:
                magic = f.read(4)
            require(magic[:2] != b"MZ" and magic not in (b"\x7fELF", b"PK\x03\x04"),
                    "Extra executable/container in native release metadata")
        inventory[name] = {"sha256": sha(p), "size": p.stat().st_size}
    require(set(names) <= inventory.keys(), "A native output is missing")
    for name, row in raw_outputs(manifest).items():
        require(inventory[name] == {k:row[k] for k in ("sha256", "size")}, "Staged native identity differs from manifest")
    require({"LICENSE", "NOTICE", "licenses/codex-LICENSE", "licenses/codex-NOTICE",
             "licenses/rust/index.json", "provenance/producer-references.json"} <= inventory.keys(),
            "Native licenses or producer receipts are missing")
    if build_lock is not None:
        require({"provenance/patches/" + name for name in admitted_patches} <= inventory.keys(),
                "Staged native patch set is incomplete for the explicit build lock")
        original = plain(root / "provenance/O165-original-build-lock.json")
        require(sha(original) == sha(selected),
                "Staged original build lock differs from the explicit build lock")
    return inventory


def make_archive(root, archive, epoch, *, build_lock=None):
    """Exact file allowlist; never archive the input/prepared runtime directory."""
    root, archive = Path(root), Path(archive)
    require(not archive.exists() and not archive.is_relative_to(root), "Archive must be new and outside staging")
    inventory = validate_tree(root, build_lock=build_lock)
    names = binary_names(load(root / "manifest.json"))
    with archive.open("xb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=epoch) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as stream:
            for name, record in sorted(inventory.items()):
                info = tarfile.TarInfo(name); info.size = record["size"]; info.mtime = epoch
                info.mode = 0o755 if name in names else 0o644
                with (root / name).open("rb") as f:
                    stream.addfile(info, f)
    with tarfile.open(archive) as stream:
        members = stream.getmembers()
        require(len(members) == len(inventory) and {m.name for m in members} == inventory.keys(),
                "Archive file set differs")
        for member in members:
            require(member.isfile() and member.size == inventory[member.name]["size"], "Archive contains a nonregular or wrong-size member")
            h = hashlib.sha256()
            with stream.extractfile(member) as f:
                for part in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(part)
            require(h.hexdigest() == inventory[member.name]["sha256"], "Archive member checksum differs")
    return {"sha256": sha(archive), "size": archive.stat().st_size, "members": len(inventory)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True, help="Already prepared locked source; never modified")
    parser.add_argument("--native-manifest", type=Path, required=True)
    parser.add_argument("--native-manifest-sha256", required=True)
    parser.add_argument("--producer-references-sha256", help="Required only for historical mixed N/J producers")
    parser.add_argument("--build-lock", type=Path, default=REPO / "native/build-lock.json")
    parser.add_argument("--cargo", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--git", default="git")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True, help="New private metadata output outside public staging")
    parser.add_argument("--development-qualification", action="store_true")
    args = parser.parse_args()
    source, output, evidence = args.source_root.resolve(), args.output.resolve(), args.evidence_dir.resolve()
    require(not output.exists() and not evidence.exists() and output != evidence
            and not output.is_relative_to(source) and not source.is_relative_to(output)
            and not output.is_relative_to(REPO) and not evidence.is_relative_to(REPO)
            and not evidence.is_relative_to(source) and not evidence.is_relative_to(output)
            and not output.is_relative_to(evidence), "Choose new independent output/evidence directories")
    require(args.cargo.is_absolute() and args.cargo_home.is_absolute(), "Explicit Cargo/cache paths required")
    raw_path = plain(args.native_manifest)
    require(sha(raw_path) == args.native_manifest_sha256, "Raw input manifest checksum differs")
    raw = load(raw_path); native = REPO / "native"
    lock_path = plain(args.build_lock)
    n_lock = load(lock_path)
    single = n_lock.get("candidate_revision") == "O165"
    spec = importlib.util.spec_from_file_location("cag_native_build", REPO / "scripts/build_native.py")
    build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    lock_digest = sha(lock_path)
    if single:
        require(args.producer_references_sha256 is None,
                "Single-source provenance derives from the exact builder manifest, not mixed references")
        graph = single_graph(n_lock, native)
        outputs = raw_outputs(raw, expected=frozenset(n_lock["binaries"]))
        producers, index, public_raw = single_sources(raw, n_lock, lock_digest, args.native_manifest_sha256, build)
    else:
        outputs = raw_outputs(raw)
        require(n_lock.get("candidate_revision") == "N" and
                lock_digest == sha(native / "build-lock.N.json"),
                "Historical mixed assembly requires the exact preserved N lock")
        require(args.producer_references_sha256 is not None, "Mixed producer reference checksum is required")
        j_lock = load(native / "build-lock.J.json")
        graph = shared_graph(n_lock, j_lock, native)
        require(raw["native_version"] == n_lock["native_version"]
                and raw["upstream_commit"] == n_lock["upstream_commit"], "Raw native baseline differs")
        source_index = source_index_location(raw_path, raw)
        metadata_dir = source_index.parent
        indexed_sources(raw, load(source_index), sha(source_index),
                        args.producer_references_sha256, n_lock, j_lock)
        references = metadata_dir / "producer-references.json"
        require(sha(plain(references)) == args.producer_references_sha256, "Producer reference checksum differs")
        producers = load(references)
        require(producers["actual_N_CLI_producer"]["source_sha256"] == n_lock["prepared_source_sha256"]
                and producers["actual_J17_retained_producer"]["source_sha256"] == j_lock["prepared_source_sha256"],
                "Native component producers differ")
        require(sha(metadata_dir / "N-original-build-lock.json") == lock_digest
                and sha(metadata_dir / "J17-original-build-lock.json") == sha(native / "build-lock.J.json"),
                "Original producer locks differ from public recipe")
        require(sha(metadata_dir / "M-original-baseline-lock.json") ==
                producers["baseline_M_source"]["build_lock_sha256"], "Retained M provenance differs")
        j_outputs = {r["name"]:r for r in producers["actual_J17_retained_producer"]["projected_original_outputs"]["outputs"]}
        for name in BINARIES - {"codex.exe"}:
            require({k:outputs[name][k] for k in ("sha256", "size")} ==
                    {k:j_outputs[name][k] for k in ("sha256", "size")}, "Retained J bytes differ")
    before = build.source_state(source)
    require(before["sha256"] == n_lock["prepared_source_sha256"]
            and before["count"] == n_lock["prepared_source_files"], "Prepared source fingerprint differs")
    lock = source / "codex-rs/Cargo.lock"
    require(sha(lock) == graph["Cargo_lock_sha256"], "Actual Cargo.lock differs")
    git = lambda *v: subprocess.check_output([args.git, "-C", str(REPO), *v]).decode("utf-8").strip()
    commit = git("rev-parse", "HEAD"); epoch = int(git("show", "-s", "--format=%ct", commit))
    dirty = bool(git("status", "--porcelain=v1", "-uall"))
    require(args.development_qualification or not dirty, "Public native assembly requires clean committed source")
    evidence.mkdir(); output.mkdir()
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith(("OPENAI", "OPENROUTER", "CODEX", "PYTHON", "PIP_", "GH_", "GITHUB_"))}
    env.update(CARGO_HOME=str(args.cargo_home), CARGO_NET_OFFLINE="true",
               CARGO_TARGET_DIR=str(evidence / "metadata-target"), RUSTC=str(args.cargo.parent / "rustc.exe"),
               PATH=str(args.cargo.parent) + os.pathsep + env.get("PATH", ""))
    command = [str(args.cargo), "metadata", "--offline", "--locked", "--format-version", "1",
               "--filter-platform", TARGET, "--manifest-path", str(source / "codex-rs/Cargo.toml")]
    result = subprocess.run(command, cwd=source, env=env, capture_output=True, timeout=120)
    (evidence / "cargo-metadata.json").write_bytes(result.stdout)
    (evidence / "cargo-metadata.stderr").write_bytes(result.stderr)
    require(result.returncode == 0, "Offline metadata failed; retain private stderr, do not download missing inputs")
    metadata = json.loads(result.stdout)
    # Public graph omits private absolute package paths while retaining exact dependency identities/features.
    public_ids = {p["id"]:p["id"] if p.get("source") else "workspace:" + p["name"] + "@" + p["version"]
                  for p in metadata["packages"]}
    graph["packages"] = sorted(({"id":p["id"], "name":p["name"], "version":p["version"], "source":p["source"]}
                                for p in metadata["packages"] if p.get("source")), key=lambda p:p["id"])
    resolved = sorted(({"id":public_ids[n["id"]], "features":sorted(n["features"]),
                        "dependencies":sorted(public_ids[x] for x in n["dependencies"])}
                       for n in metadata["resolve"]["nodes"]), key=lambda n:n["id"])
    graph["resolved_nodes"] = resolved
    graph["resolve_sha256"] = hashlib.sha256(canonical(resolved)).hexdigest()
    graph["scope"] = ("Single-source fixed target external workspace graph, a build/test-inclusive superset" if single
                      else "Common N/J target external workspace graph, a build/test-inclusive superset")
    for name, row in outputs.items():
        require((raw_path.parent / name).stat().st_size == row["size"], "Native input size differs")
        copy_bytes(raw_path.parent / name, output / name, row["sha256"])
    copy_bytes(REPO / "LICENSE", output / "LICENSE")
    copy_bytes(REPO / "NOTICE", output / "NOTICE")
    for name in ("LICENSE", "NOTICE"):
        copy_bytes(source / name, output / "licenses" / ("codex-" + name))
    licenses = collect_licenses(metadata, output / "licenses/rust", [args.cargo_home, source], git=args.git)
    write(output / "provenance/dependency-graph.json", graph)
    copy_bytes(lock, output / "provenance/Cargo.lock", graph["Cargo_lock_sha256"])
    if single:
        write(output / "provenance/producer-references.json", producers)
        write(output / "provenance/native-source-index.json", index)
        write(output / "provenance/original-raw-manifest.json", public_raw)
        copy_bytes(lock_path, output / "provenance/O165-original-build-lock.json", lock_digest)
    else:
        copy_bytes(references, output / "provenance/producer-references.json", args.producer_references_sha256)
        copy_bytes(raw_path, output / "provenance/original-raw-manifest.json", args.native_manifest_sha256)
        for name in ("N-original-build-lock.json", "J17-original-build-lock.json", "M-original-baseline-lock.json"):
            copy_bytes(metadata_dir / name, output / "provenance" / name)
        copy_bytes(source_index, output / "provenance/native-source-index.json", raw["source_index"]["sha256"])
    for row in n_lock["patches"]:
        copy_bytes(native / row["file"], output / "provenance/patches" / row["file"], row["sha256"])
    write(output / "provenance/source.json", {
        "schema_version":1, "repository":"https://github.com/0xZoharHuang/codex-app-deepseek-gateway",
        "source_commit":commit, "source_dirty":dirty,
        "kind":"development_qualification" if args.development_qualification else "clean_source_assembly",
        "source_date_epoch":epoch, "native_upstream_commit":n_lock["upstream_commit"],
        "native_archive_sha256":n_lock["archive_sha256"],
        "native_archive_url":"https://codeload.github.com/openai/codex/tar.gz/" + n_lock["upstream_commit"],
        "prepared_source_sha256":before["sha256"],
        "producer_layout":"single_O165" if single else "mixed_N_J",
        "historical_J_source_sha256":None if single else j_lock["prepared_source_sha256"],
        "original_raw_manifest_sha256":args.native_manifest_sha256,
        "packager_sha256":sha(__file__), "no_native_compile_or_execution":True,
        "sidecar_policy":("All six native executables, including Code Mode Host, share the pinned source producer; no closed App executable is redistributed." if set(outputs) == SOURCE_BINARIES else "Obtain required Code Mode Host locally through public app-prepare-runtime; not redistributed here.")})
    manifest = copy.deepcopy(public_raw if single else raw)
    if not single:
        manifest["source_index"] = {"file":"provenance/native-source-index.json", "sha256":sha(output / "provenance/native-source-index.json")}
    manifest["distribution"] = {"schema_version":1, "kind":("raw_six_binary_distribution" if set(outputs) == SOURCE_BINARIES else "raw_four_binary_distribution"),
        "license_index":{"file":"licenses/rust/index.json", "sha256":sha(output / "licenses/rust/index.json")},
        "producer_references":{"file":"provenance/producer-references.json", "sha256":sha(output / "provenance/producer-references.json")},
        "native_build_executed":False, "App_acceptance_asserted":False}
    write(output / "manifest.json", manifest)
    after = build.source_state(source)
    require(after == before and sha(raw_path) == args.native_manifest_sha256
            and sha(lock_path) == lock_digest
            and (single or sha(references) == args.producer_references_sha256), "Original source/producers changed")
    for row in n_lock["patches"]:
        require(sha(native / row["file"]) == row["sha256"], "Native patch changed during packaging")
    require(git("rev-parse", "HEAD") == commit and bool(git("status", "--porcelain=v1", "-uall")) == dirty,
            "Gateway source state changed during packaging")
    inventory = validate_tree(output, build_lock=lock_path if single else None)
    write(output / "provenance/distribution-inventory.json", inventory)
    suffix = ".qualification.tar.gz" if args.development_qualification else ".tar.gz"
    archive = output.parent / (output.name + suffix)
    packed = make_archive(output, archive, epoch, build_lock=lock_path if single else None)
    report = {"status":"raw_native_assembled_not_App_qualified", "output":str(output), "archive":str(archive),
              "archive_identity":packed, "manifest_sha256":sha(output / "manifest.json"),
              "raw_binary_count":len(outputs), "external_crates":len(licenses["packages"]),
              "no_official_App_or_prepared_sidecar":True, "no_compile_or_native_execution":True,
              "original_source_and_receipts_unchanged":True,
              "kind":"development_qualification" if args.development_qualification else "clean_source_assembly"}
    write(evidence / "assembly-result.json", report)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print("Native assembly rejected: " + str(error))
        raise SystemExit(1)
