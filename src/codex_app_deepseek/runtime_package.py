"""Strict local descriptor for an installed own MSIX runtime; no deployment or signing."""
from pathlib import Path
import hashlib
import json
import re

from .paths import _is_link_or_reparse

KIND = "cadsg-native-msix"
PACKAGE_NAME = "CADSG.NativeRuntime"
BINARIES = frozenset({
    "codex.exe", "codex-code-mode-host.exe", "codex-command-runner.exe",
    "codex-windows-sandbox-setup.exe", "codex-windows-managed-deny-probe.exe",
    "codex-windows-sandbox-service.exe",
})
MODES = {
    "registered_core": ("RuntimeRegisteredCore", "cadsg-native.exe"),
    "legacy": ("RuntimeLegacy", "cadsg-native-legacy.exe"),
}
APPEXECLINK = 0x8000001B

class RuntimePackageError(ValueError):
    pass

def require(value, message):
    if not value:
        raise RuntimePackageError(message)

def checksum(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def _pairs(values):
    result = {}
    for key, value in values:
        require(key not in result, "duplicate runtime descriptor key")
        result[key] = value
    return result

def load(path, expected_sha256):
    path = Path(path)
    require(re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or "") is not None,
            "invalid runtime descriptor checksum")
    require(path.is_file() and path.stat().st_size <= 65536
            and not any(_is_link_or_reparse(part) for part in (path, *path.parents)),
            "runtime descriptor path is missing, redirected or oversized")
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected_sha256,
            "runtime descriptor checksum mismatch")
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(RuntimePackageError("nonfinite runtime descriptor value")))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimePackageError("invalid runtime descriptor JSON") from error

def verify(path, expected_sha256, expected_app, owner, home, backend):
    descriptor = load(path, expected_sha256)
    require(set(descriptor) == {
        "schema_version", "kind", "status", "mode", "native_version", "upstream_commit",
        "source_sha256", "build_lock_sha256", "app_baseline_sha256", "owner_sid", "home",
        "package", "outputs",
    } and descriptor["schema_version"] == 1 and type(descriptor["schema_version"]) is int
            and descriptor["kind"] == KIND and descriptor["status"] == "installed-not-app-qualified",
            "unsupported runtime package descriptor")
    require(isinstance(descriptor["mode"], str) and descriptor["mode"] in MODES,
            "unknown runtime activation mode")
    require(descriptor["native_version"] == expected_app["native_version"]
            and descriptor["upstream_commit"] == expected_app["native_upstream_commit"],
            "runtime package does not match the fixed native version")
    for name in ("source_sha256", "build_lock_sha256", "app_baseline_sha256"):
        require(isinstance(descriptor[name], str) and re.fullmatch(r"[0-9a-f]{64}", descriptor[name]),
                "runtime package provenance is missing")
    from .isolated_app import BASELINE_SHA256
    require(descriptor["app_baseline_sha256"] == BASELINE_SHA256,
            "runtime descriptor belongs to another App baseline")
    require(descriptor["owner_sid"] == owner["sid"] and Path(descriptor["home"]).resolve() == Path(home).resolve(),
            "runtime descriptor owner or dedicated home differs")
    package = descriptor["package"]
    require(isinstance(package, dict) and set(package) == {
        "name", "full_name", "family", "version", "publisher", "signature_kind",
        "signer_thumbprint", "manifest_sha256", "signature_sha256",
    } and package["name"] == PACKAGE_NAME
            and all(isinstance(value, str) and value and not any(c in value for c in "\x00\r\n")
                    for value in package.values()), "runtime package identity is incomplete")
    require(isinstance(package["family"], str)
            and package["family"].rsplit("_", 1)[0] == PACKAGE_NAME
            and isinstance(package["full_name"], str)
            and package["full_name"].startswith(PACKAGE_NAME + "_"),
            "runtime package belongs to another family")
    require(package["signature_kind"] in {"Developer", "Enterprise", "Store"}
            and re.fullmatch(r"[0-9A-Fa-f]{40,64}", package["signer_thumbprint"] or "") is not None,
            "runtime package signer identity is missing")
    for name in ("manifest_sha256", "signature_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", package[name] or "") is not None,
                "runtime package signature/manifest binding is missing")
    rows = descriptor["outputs"]
    require(isinstance(rows, list) and len(rows) == len(BINARIES),
            "runtime package must identify all six own native producers")
    files = {}
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"name", "size", "sha256", "path"},
                "invalid runtime package output")
        name = row["name"]
        require(name in BINARIES and name not in files
                and type(row["size"]) is int and row["size"] > 0
                and re.fullmatch(r"[0-9a-f]{64}", row["sha256"] or "") is not None
                and isinstance(row["path"], str) and Path(row["path"]).is_absolute(),
                "invalid or duplicate runtime package binary")
        files[name] = dict(row)
    observed = backend.runtime_package_document(descriptor)
    actual = observed["package"]
    for key in ("name", "full_name", "family", "version", "publisher", "signature_kind"):
        require(actual.get(key) == package[key], "installed runtime package identity differs")
    require(actual.get("status") == "Ok" and actual.get("development") is False,
            "runtime is not a healthy signed installed package")
    require(observed.get("package_signature_status") == "Valid"
            and observed.get("signer_thumbprint", "").upper() == package["signer_thumbprint"].upper(),
            "runtime package signature is not valid or signer differs")
    location = Path(actual["location"])
    require(location.is_absolute() and not any(_is_link_or_reparse(part) for part in (location, *location.parents)),
            "installed runtime package directory is redirected")
    expected_hashes = {"AppxManifest.xml": package["manifest_sha256"],
                       "AppxSignature.p7x": package["signature_sha256"]}
    for name, row in files.items():
        binary = location / "runtime" / name
        require(Path(row["path"]).resolve() == binary.resolve()
                and binary.is_file()
                and not any(_is_link_or_reparse(part) for part in (binary, *binary.parents))
                and binary.stat().st_size == row["size"] and checksum(binary) == row["sha256"],
                "runtime native image path/bytes differ")
        expected_hashes["runtime/" + name] = row["sha256"]
    require(observed["files"] == expected_hashes, "installed runtime package bytes differ")
    application_id, alias = MODES[descriptor["mode"]]
    activation = observed["activation"]
    require(activation["alias"] == alias and activation["application_id"] == application_id
            and activation["executable"] == r"runtime\codex.exe"
            and activation["reparse_tag"] == APPEXECLINK,
            "runtime alias role, executable or OS alias tag differs")
    alias_path = Path(activation["path"])
    alias_directory = Path(activation["known_alias_directory"])
    require(alias_path.is_absolute() and alias_path.parent == alias_directory
            and alias_path.name == alias and alias_path.is_file() and alias_directory.name == package["family"]
            and not any(_is_link_or_reparse(part) for part in (alias_directory, *alias_directory.parents)),
            "runtime alias is outside the exact OS-known package-qualified directory")
    return {"manifest": str(Path(path).resolve()), "manifest_sha256": expected_sha256,
            "source_sha256": descriptor["source_sha256"], "build_lock_sha256": descriptor["build_lock_sha256"],
            "native_version": descriptor["native_version"], "upstream_commit": descriptor["upstream_commit"],
            "files": files, "runtime_package": {
                "package": package, "mode": descriptor["mode"],
                "descriptor_path": str(Path(path).resolve()), "descriptor_sha256": expected_sha256,
                "application_id": application_id, "alias_path": str(alias_path),
                "alias_name": alias, "install_location": str(location),
            }}

def validate_control_binding(runtime, executable, environment):
    require(Path(executable).resolve() == (Path(runtime["install_location"]) / "runtime/codex.exe").resolve(),
            "runtime control image differs from installed package")
    require(environment["CODEX_CLI_PATH"] == runtime["alias_path"],
            "runtime control alias selector differs")

def verify_activation(runtime, observed, role="cli"):
    package = runtime["package"]
    require(observed.get("family") == package["family"]
            and observed.get("full_name") == package["full_name"],
            "native process has another kernel package identity")
    app = observed.get("application_id")
    require(isinstance(app, str) and app.startswith(package["family"] + "!"),
            "native process has no matching kernel application ID")
    allowed = {runtime["application_id"]}
    if role == "host":
        allowed.add("CodeModeHostRegistered" if runtime["mode"] == "registered_core" else "CodeModeHostLegacy")
    require(app.split("!", 1)[1] in allowed, "native process activation mode or role differs")


def require_mode_available(runtime, snapshot, owner, home):
    """Honor the existing native one-owner/Core teardown record; never mutate it."""
    if snapshot is None:
        return
    require(isinstance(snapshot, dict), "native runtime owner state is unverified")
    if snapshot.get("state_absent") is True:
        return
    require(type(snapshot.get("runtime_present")) is bool,
            "native runtime owner state is unverified")
    if not snapshot["runtime_present"]:
        return
    same_owner = snapshot.get("owner_sid") == owner["sid"]
    same_home = same_owner and isinstance(snapshot.get("home"), str) and (
        Path(snapshot["home"]).resolve() == Path(home).resolve())
    is_core = runtime is not None and runtime.get("mode") == "registered_core"
    if is_core:
        require(same_home, "RegisteredCore service already belongs to another owner/home")
    else:
        require(not same_home,
                "RegisteredCore owns this native home; complete native package teardown before Legacy rebind/launch")
