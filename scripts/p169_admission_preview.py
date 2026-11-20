"""Read-only P169 admission preview. A valid receipt does not qualify the App."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys

# Keep the same imports for direct and isolated Python execution.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_native as build
import package_native_release as package


DRAFT = Path(__file__).resolve().parents[1] / "native/p169-admission.draft.json"


def object_without_duplicates(items):
    result = {}
    for key, value in items:
        package.require(key not in result, "Duplicate JSON field: " + key)
        result[key] = value
    return result


DECODER = json.JSONDecoder(object_pairs_hook=object_without_duplicates)


def pinned_file(path, digest):
    path = package.plain(path.absolute())
    package.require(package.sha(path) == digest, "Pinned SHA-256 differs: " + path.name)
    return path


def normalized_stock(path, expected):
    source = pinned_file(path, expected["readback_sha256"]).read_text(encoding="utf-8-sig")
    value, end = DECODER.raw_decode(source.lstrip())
    tail = source.lstrip()[end:].strip()
    # The archived PowerShell receipt ends with a literal backslash-n.
    package.require(tail in ("", r"\n"), "Unexpected signed Store receipt suffix")
    digest = hashlib.sha256(package.canonical(value)).hexdigest()
    package.require(digest == expected["normalized_sha256"], "Normalized Store receipt differs")
    fields = ("package_full_name", "package_family_name", "publisher", "exe_thumbprint")
    package.require(all(value[key] == expected[key] for key in fields)
                    and value["exe_signature"] == value["package_signature_status"] == "Valid"
                    and value["app_launches"] == 0 and value["production_app_changed"] is False,
                    "Signed stock identity differs")
    missing = [row["path"] for row in value["files"] if row.get("missing") is True]
    package.require(missing == expected["observed_missing_resources"],
                    "Unexpected change in observed stock resource availability")
    return value


def checked_profile(path, expected, stock):
    readback = expected["frontend_readback"]
    value = DECODER.decode(pinned_file(path, readback["sha256"])
                          .read_text(encoding="utf-8-sig").strip())
    asar, = (row for row in stock["files"] if row["path"] == "app/resources/app.asar")
    package.require(value["package"] == stock["package_full_name"]
                    and value["asar_sha256"] == asar["sha256"]
                    and value["actual_profile_coexistence_established"] is False,
                    "Static App profile evidence belongs to a different Store install")
    entries = {row["entry"]: row for row in value["entries"]}
    user_data = entries[readback["user_data_entry"]]
    backend = entries[readback["backend_selector_entry"]]
    package.require(user_data["setter_text_before_lock"] is True
                    and 0 <= user_data["user_data_set_at"] < user_data["marker_offsets"]["requestSingleInstanceLock"]
                    and all(backend["marker_offsets"][name] >= 0
                            for name in [*expected["private_paths"], *expected["backend_selector"]]),
                    "Static App profile markers differ from the draft selectors")
    return {
        "asar_sha256": value["asar_sha256"],
        "static_entries": [user_data["entry"], backend["entry"]],
        "actual_profile_coexistence_established": False,
    }


def checked_inventory(root, expected, package_full_name):
    path = pinned_file(root / expected["file"], expected["sha256"])
    value = DECODER.decode(path.read_text(encoding="utf-8-sig").strip())
    fields = ("source_relative", "plugin_id", "plugin_version", "files_count", "total_bytes", "fingerprint")
    package.require(all(value[key] == expected[key] for key in fields)
                    and value["schema_version"] == 1 and value["marketplace_name"] == "openai-bundled"
                    and value["package_full_name"] == package_full_name,
                    "Bundled App tools do not belong to the signed package")
    files = value["files"]
    digest = hashlib.sha256(package.canonical(files)).hexdigest()
    package.require(digest == value["fingerprint"] and len(files) == value["files_count"]
                    and sum(row["size"] for row in files.values()) == value["total_bytes"],
                    "Bundled App tools inventory is inconsistent")
    return {key: value[key] for key in fields}


def checked_recipe(root, source, expected, *, patch_dir=None):
    lock = DECODER.decode(pinned_file(root / expected["file"], expected["sha256"])
                          .read_text(encoding="utf-8-sig").strip())
    fields = ("candidate_revision", "upstream_commit", "native_version", "package_mode")
    package.require(all(lock[key] == expected[key] for key in fields) and lock["schema_version"] == 1
                    and lock["release_environment"] == build.PROFILE and lock["target"] == package.TARGET
                    and len(lock["binaries"]) == len(package.SOURCE_BINARIES)
                    and set(lock["binaries"]) == package.SOURCE_BINARIES,
                    "Pinned P169 native recipe differs")
    pinned_file(source / "codex-rs/Cargo.lock", lock["cargo_lock_sha256"])
    for patch in lock["patches"]:
        name = patch["file"]
        package.require(PurePosixPath(name).name == name and Path(name).name == name
                        and "\\" not in name and ":" not in name, "Invalid native patch filename")
        pinned_file((patch_dir or root) / name, patch["sha256"])
    return lock


def v8_identity(value):
    fields = ("version", "profile", "target", "source_manifest_sha256")
    files = {Path(row["file"]).name: {"sha256": row["sha256"], "size": row["size"]}
             for row in value["files"]}
    package.require(len(files) == len(value["files"]), "Duplicate native V8 input")
    return {**{key: value[key] for key in fields}, "files": files}


def checked_raw(path, digest, lock, draft):
    path = pinned_file(path, digest)
    raw = DECODER.decode(path.read_text(encoding="utf-8-sig").strip())
    recipe = draft["native_recipe"]
    package.require(raw["mode"] == "build" and raw["runtime_executed"] is False and raw["app_qualified"] is False
                    and raw["msvc_sdk_preflight"] == {
                        "status": "compile-link-passed", "target": lock["target"],
                        "probe_program_executed": False},
                    "Raw P169 receipt is not from the expected compile-only build")
    package.require(v8_identity(raw["rusty_v8"]) == draft["official_v8"],
                    "Raw P169 receipt used different official V8 inputs")
    _, _, bundle = package.single_sources(raw, lock, recipe["sha256"], digest, build)
    package.require(bundle.get("runtime_mode") == recipe["runtime_mode"]
                    and bundle.get("kind") == package.SOURCE_BUNDLE_KIND,
                    "Native P169 candidate differs from the approved runtime layout")
    package.require(Path(raw["output"]).is_absolute() and Path(raw["output"]) == path.parent,
                    "Raw receipt is not in its recorded native output directory")
    for row in bundle["outputs"]:
        output = pinned_file(path.parent / row["name"], row["sha256"])
        package.require(output.stat().st_nlink == 1 and output.stat().st_size == row["size"],
                        "Native result has an unexpected link or size: " + row["name"])
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--v8-dir", type=Path, required=True)
    parser.add_argument("--patch-dir", type=Path)
    parser.add_argument("--frontend-preflight", type=Path, required=True)
    parser.add_argument("--raw-manifest", type=Path)
    parser.add_argument("--raw-manifest-sha256")
    args = parser.parse_args()
    if (args.raw_manifest is None) != (args.raw_manifest_sha256 is None):
        parser.error("Both --raw-manifest and --raw-manifest-sha256 are required for compiled inputs")
    draft = DECODER.decode(DRAFT.read_text(encoding="utf8").strip())
    package.require(draft["schema_version"] == 1 and draft["status"] == "draft-only", "Invalid draft")
    stock = normalized_stock(args.evidence_root / "signed-store-inputs.json", draft["signed_store"])
    profile = checked_profile(args.frontend_preflight, draft["private_profile_gate"], stock)
    inventory = checked_inventory(args.evidence_root, draft["bundled_inventory"], stock["package_full_name"])
    lock = checked_recipe(args.evidence_root, args.source, draft["native_recipe"], patch_dir=args.patch_dir)
    v8 = build.verified_rusty_v8_inputs(args.source, args.v8_dir, lock["target"])
    package.require(v8_identity(v8) == draft["official_v8"], "Pinned P169 V8 identity differs")
    native = None
    if args.raw_manifest is not None:
        native = checked_raw(args.raw_manifest, args.raw_manifest_sha256, lock, draft)
    print(json.dumps({
        "status": "draft-native-inputs-verified" if native else "draft-static-inputs-verified",
        "app_qualified": False,
        "app_launch_allowed": False,
        "signed_stock_readback": stock,
        "bundled_inventory": inventory,
        "native_recipe": lock,
        "official_v8": v8_identity(v8),
        "native_build_readback": native,
        "private_profile_gate": draft["private_profile_gate"],
        "static_profile_readback": profile,
        "remaining_app_gates": draft["remaining_app_gates"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
