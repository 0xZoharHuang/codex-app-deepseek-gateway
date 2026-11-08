"""Offline admission and receipt checks. This suite never starts Cargo or an App."""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("p20-windows.py")
SPEC = importlib.util.spec_from_file_location("p167_test_controller", SCRIPT)
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


class P167Gate(unittest.TestCase):
    def test_pending_review_cannot_start(self):
        pending = {"status": "pending"}
        with self.assertRaisesRegex(ValueError, "must be sealed"):
            ci.select_p167(pending)

    def test_sealed_review_cannot_omit_or_relax_native_selectors(self):
        good = dict(status="sealed", lock_sha256="1" * 64, source_sha256="2" * 64,
                    builder_sha256="3" * 64, packager_sha256="4" * 64,
                    workflow_sha256="5" * 64, source_files=9850,
                    capacity_cases=["session::tests::fixture_exact_capacity"])
        self.assertEqual(ci.reviewed_p167_plan(good)["codex-core"],
                         (*ci.P167_GUARDIAN_CASES["codex-core"], *good["capacity_cases"]))
        for bad in ({"source_files": True}, {"source_sha256": ""},
                    {"capacity_cases": []}, {"capacity_cases": ["partial"]},
                    {"capacity_cases": good["capacity_cases"] * 2},
                    {"capacity_cases": list(ci.P167_GUARDIAN_CASES["codex-core"])}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ci.reviewed_p167_plan({**good, **bad})

    def test_exact_manual_scope_two_jobs_and_new_cache(self):
        env = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
               "RUNNER_OS": "Windows", "RUNNER_ARCH": "X64", "CI_REPOSITORY_VISIBILITY": "public",
               "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REF": ci.P167_BRANCH,
               "GITHUB_EVENT_NAME": "workflow_dispatch", "CI_P167_APPROVED_LOCK_SHA256": "1" * 64,
               "CI_P20_BUILD_JOBS": "2"}
        with patch.multiple(ci, CANDIDATE="P167", LOCK="1" * 64):
            self.assertEqual(ci.hosted_context(env, "nt"), ("123", "1"))
            self.assertEqual(ci.cache_paths(Path("C:/cagp167-123-1"))[0].name, "c167")
            for key in env:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    ci.hosted_context({**env, key: "wrong"}, "nt")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "must-not-be-created"
            # The negative control represents a local invocation even when
            # unittest itself runs inside the approved hosted job.
            local = {key: value for key, value in os.environ.items()
                     if key not in {"GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "GITHUB_REF", "GITHUB_EVENT_NAME"}}
            run = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "prepare",
                                  "--candidate", "P167", "--root", str(target)],
                                 text=True, capture_output=True, check=False, env=local)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("exact disposable GitHub-hosted branch", run.stderr)
            self.assertFalse(target.exists())

    def test_all_exact_receipts_and_raw_evidence_gate_assembly(self):
        guardian = {"codex-core": ("session::tests::fixture_capacity",),
                    "codex-guardian-v2": ("async_scorer::tests::fixture_guardian",)}
        with tempfile.TemporaryDirectory() as directory, patch.multiple(
            ci, CANDIDATE="P167", LOCK="1" * 64, SOURCE="2" * 64, P167_CASES=guardian,
        ), patch.dict(os.environ, {"CI_P167_TEST_RESUME": "0"}):
            root = Path(directory)
            (root / "evidence").mkdir()
            (root / "delivery/unit").mkdir(parents=True)
            exe = root / "delivery/unit/fixture.exe"
            exe.write_bytes(b"synthetic fixture")
            specs = {
                "native-unit18.json": ("P167_NATIVE_COMPONENT18_PASSED_NOT_APP_QUALIFIED",
                    ["sandbox::fixture"], "native-case"),
                "native-git-unit12.json": ("P167_NATIVE_GIT12_PASSED_NOT_APP_QUALIFIED",
                    [f"{m}::git_lifetime_windows_tests::{c}" for m in ("startup_sync", "marketplace_upgrade::git")
                     for c in ci.P21_GIT_CASES], "native-git-case"),
                **{f"native-p167-{group}.json": ("P167_EXACT_NATIVE_UNIT_PASSED_NOT_APP_QUALIFIED",
                      cases, f"native-p167-{group}-case") for group, cases in guardian.items()},
            }
            for file, (status, names, prefix) in specs.items():
                rows = []
                for index, name in enumerate(names):
                    log = root / "evidence" / f"{prefix}-{index:02}.log"
                    log.write_text(f"test {name} ... ok\n"
                                   "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured;\n")
                    rows.append({"name": name, "log_sha256": ci.sha(log)})
                ci.save(root / "evidence" / file, {
                    "status": status, "source_sha256": ci.SOURCE, "lock_sha256": ci.LOCK,
                    "App_GUI_qualified": False, "cases": rows,
                    "executable": {"path": str(exe), "sha256": ci.sha(exe), "profile": {"test": True}},
                })
            self.assertEqual(set(ci.verify_p167_unit_receipts(root, ["sandbox::fixture"])), set(specs))
            gate = root / "evidence/native-p167-codex-core.json"
            good_gate = gate.read_bytes()
            gate.unlink()
            with self.assertRaises(FileNotFoundError):
                ci.verify_p167_unit_receipts(root, ["sandbox::fixture"])
            gate.write_bytes(good_gate)
            log = root / "evidence/native-p167-codex-core-case-00.log"
            log.write_text("test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured;\n")
            forged = json.loads(good_gate)
            forged["cases"][0]["log_sha256"] = ci.sha(log)
            gate.write_text(json.dumps(forged))
            with self.assertRaisesRegex(ValueError, "filtered-out pass"):
                ci.verify_p167_unit_receipts(root, ["sandbox::fixture"])

    def test_real_git_windows_filters_keep_the_exact_workflow_digest(self):
        name = ".github/workflows/p20-native-windows.yml"
        git = shutil.which("git.exe") or shutil.which("git")
        if git is None and os.name == "nt":
            git = str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/cmd/git.exe")
        checked = subprocess.check_output([git, "-C", str(ci.REPO), "-c", "core.autocrlf=true",
                                           "cat-file", "--filters", f"--path={name}", f"HEAD:{name}"])
        self.assertEqual(checked, (ci.REPO / name).read_bytes())
        self.assertEqual(ci.sha(ci.REPO / name),
                         ci.load(ci.REPO / "scripts/ci/p167-review.json")["workflow_sha256"])

    def test_scoped_git_reads_longpaths_and_short_cache_covers_observed_file(self):
        git = shutil.which("git.exe") or shutil.which("git")
        if git is None and os.name == "nt":
            git = str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/cmd/git.exe")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ci.p167_git_config(root)
            config.parent.mkdir(parents=True)
            config.write_bytes(ci.P167_GIT_CONFIG)
            supplied = {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.longpaths",
                        "GIT_CONFIG_VALUE_0": "false", "GIT_CONFIG_GLOBAL": "foreign"}
            environment = ci.p167_environment(root, supplied)
            self.assertEqual(supplied["GIT_CONFIG_GLOBAL"], "foreign")
            self.assertNotIn("GIT_CONFIG_COUNT", environment)
            self.assertEqual(Path(environment["XDG_CONFIG_HOME"]) / "git/config", config)
            value = subprocess.check_output([git, "config", "--global", "--bool", "--get", "core.longpaths"],
                                            env=environment, text=True).strip()
            self.assertEqual(value, "true")
            config.write_bytes(b"[core]\n\tlongpaths = false\n")
            with self.assertRaises(ValueError):
                ci.p167_environment(root, supplied)
        member = ci.P167_V8_CHECKOUT / ci.P167_LIBCXX / ci.P167_LONG_PATH
        old = PureWindowsPath(r"C:\cagp167-native-cache-v1\cargo") / member.as_posix()
        new = PureWindowsPath(r"C:\c167\cargo") / member.as_posix()
        self.assertEqual((len(str(old)), len(str(new))), (262, 243))

    def test_producer_refuses_missing_or_changed_dependency_evidence(self):
        with tempfile.TemporaryDirectory() as directory, patch.multiple(
            ci, CANDIDATE="P167", LOCK="1" * 64, SOURCE="2" * 64,
        ), patch.dict(ci.os.environ, {"CI_P167_RESUME": "0"}):
            root = Path(directory)
            cache = root / "short-cache"
            target = cache / "target"
            target.mkdir(parents=True)
            (root / "evidence").mkdir()
            config = ci.p167_git_config(root)
            config.parent.mkdir(parents=True)
            config.write_bytes(ci.P167_GIT_CONFIG)
            toolchain = root / "rust"
            lock = {"cargo_lock_sha256": "3" * 64, "target": "x86_64-pc-windows-msvc"}
            with patch.object(ci, "cache_paths", return_value=(cache, target)), patch.object(
                ci, "p167_checkout_proof", return_value={"path": "observed"},
            ):
                with self.assertRaises(FileNotFoundError):
                    ci.require_p167_dependency_checkout(root, lock, toolchain)
                log = root / "evidence/p167-locked-fetch.log"
                log.write_bytes(b"synthetic observer log")
                observer = root / "evidence/p167-locked-fetch.json"
                ci.save(observer, {"passed": True})
                evidence = root / "evidence/p167-dependency-checkout.json"
                ci.save(evidence, {
                    "status": "LOCKED_FETCH_CHECKOUT_PROVEN", "build_lock_sha256": ci.LOCK,
                    "source_sha256": ci.SOURCE, "cargo_lock_sha256": lock["cargo_lock_sha256"],
                    "cargo_home": str(cache / "cargo"), "cargo": str(toolchain / "cargo.exe"),
                    "target": lock["target"], "checkout": {"path": "observed"},
                    "fetch_metadata_dir": str(root / "dependency-preflight/target-metadata"),
                    "git_configuration_sha256": ci.sha(config), "fetch_log_sha256": ci.sha(log),
                    "fetch_observer_sha256": ci.sha(observer),
                })
                self.assertEqual(ci.require_p167_dependency_checkout(root, lock, toolchain)["CARGO_NET_OFFLINE"],
                                 "true")
                good = evidence.read_bytes()
                wrong = json.loads(good)
                wrong["fetch_metadata_dir"] = str(target)
                evidence.write_text(json.dumps(wrong))
                with self.assertRaises(ValueError):
                    ci.require_p167_dependency_checkout(root, lock, toolchain)
                evidence.write_bytes(good)
                (target / ".rustc_info.json").write_bytes(b"simulated leaked metadata")
                with self.assertRaises(ValueError):
                    ci.require_p167_dependency_checkout(root, lock, toolchain)
                (target / ".rustc_info.json").unlink()
                log.write_bytes(b"mutated receipt")
                with self.assertRaises(ValueError):
                    ci.require_p167_dependency_checkout(root, lock, toolchain)

    def test_resume_cache_miss_and_wrong_artifact_fail_closed(self):
        resume = ci.load_module("p167_original_resume_tests", ci.REPO / "scripts/ci/p167-resume.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "c167"
            for bad in ("wrong", resume.CACHE_KEY):
                with patch.dict(os.environ, {"CI_P167_RESUME": "1", "CI_P167_CACHE_KEY": bad,
                                             "CI_P167_CACHE_HIT": "false" if bad == resume.CACHE_KEY else "true"}):
                    with self.assertRaisesRegex(ValueError, "cache restore required"):
                        resume.admit_cache(root, {}, "", "", cache, cache / "target")
            artifact = root / "changed.zip"
            artifact.write_bytes(b"wrong bytes")
            with self.assertRaisesRegex(ValueError, "artifact bytes required"):
                resume.verify_archive(artifact)

    def test_resume_v8_inputs_require_original_path_and_exact_bytes(self):
        resume = ci.load_module("p167_original_resume_tests", ci.REPO / "scripts/ci/p167-resume.py")
        builder = ci.load_module("p167_v8_copy_test_builder", ci.REPO / "scripts/build_native.py")
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            root = fixture / "new-run"
            old_work = fixture / "original-run/work"
            original = resume.original_v8_directory(old_work)
            downloaded = root / "inputs/rusty-v8"
            downloaded.mkdir(parents=True)
            source = root / "dependency-preflight/source"
            (source / "codex-rs").mkdir(parents=True)
            (source / "codex-rs/Cargo.lock").write_text('[[package]]\nname="v8"\nversion="152.2.0"\n')
            lock_pin = source / "third_party/v8/rusty_v8_152_2_0_release_manifests.sha256"
            lock_pin.parent.mkdir(parents=True)
            target = "x86_64-pc-windows-msvc"
            suffix = f"ptrcomp_sandbox_release_{target}"
            archive = downloaded / f"rusty_v8_{suffix}.lib.gz"
            binding = downloaded / f"src_binding_{suffix}.rs"
            archive.write_bytes(b"fixture V8 library")
            binding_bytes = b"fixture bindings"
            binding.write_bytes(binding_bytes)
            manifest = downloaded / f"rusty_v8_{suffix}.sha256"
            manifest.write_text("".join(f"{ci.sha(p)}  {p.name}\n" for p in (archive, binding)))
            lock_pin.write_text(f"{ci.sha(manifest)}  {manifest.name}\n")
            with patch.object(resume, "original_work", return_value=old_work):
                with self.assertRaisesRegex(ValueError, "original V8 directory"):
                    resume.stage_v8_inputs(root, builder, target, {"original_v8_directory": str(downloaded)})
                self.assertFalse(old_work.parent.exists())
                admitted = {"original_v8_directory": str(original)}
                binding.write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "Official V8 checksum mismatch"):
                    resume.stage_v8_inputs(root, builder, target, admitted)
                self.assertFalse(old_work.parent.exists())
                binding.write_bytes(binding_bytes)
                source_times = {p: p.stat().st_mtime_ns for p in (archive, binding, manifest, lock_pin)}
                receipt = resume.stage_v8_inputs(root, builder, target, admitted)
                self.assertEqual(receipt["directory"], str(original))
                self.assertEqual(receipt["environment"], {
                    "RUSTY_V8_ARCHIVE": str(original / archive.name),
                    "RUSTY_V8_SRC_BINDING_PATH": str(original / binding.name),
                })
                self.assertFalse(old_work.exists())
                for p in (archive, binding, manifest):
                    self.assertEqual(ci.sha(original / p.name), ci.sha(p))
                self.assertEqual(source_times, {p: p.stat().st_mtime_ns for p in source_times})
                with self.assertRaises(FileExistsError):
                    resume.stage_v8_inputs(root, builder, target, admitted)

    def test_p167_v8_download_uses_exact_official_public_asset_requests(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ci, "CANDIDATE", "P167"):
            root = Path(directory)
            for name, (asset_id, size) in ci.P167_V8_ASSETS.items():
                with self.subTest(name=name), patch.object(ci, "download", return_value={"bytes": size}) as download:
                    digest = ci.P167_V8_FILES.get(name, ci.P167_V8_MANIFEST_SHA)
                    result = ci.download_v8(ci.P167_V8_RELEASE, root / name, digest)
                    url = f"https://api.github.com/repos/openai/codex/releases/assets/{asset_id}"
                    download.assert_called_once_with(url, root / name, digest, expected_size=size, headers={
                        "Accept": "application/octet-stream", "X-GitHub-Api-Version": "2022-11-28",
                        "Cache-Control": "no-cache",
                    })
                    self.assertEqual(result["asset_id"], asset_id)
                    self.assertEqual(result["release_url"], f"{ci.P167_V8_RELEASE}/{name}")
                    self.assertEqual(result["transport"], "github-release-asset-api")
            name = next(iter(ci.P167_V8_ASSETS))
            with patch.object(ci, "download") as download:
                for base, file, digest in ((ci.P167_V8_RELEASE, name, "wrong digest"),
                    (ci.P167_V8_RELEASE, "other.sha256", ci.P167_V8_MANIFEST_SHA),
                    ("https://example.invalid", name, ci.P167_V8_MANIFEST_SHA)):
                    with self.assertRaises(ValueError):
                        ci.download_v8(base, root / file, digest)
                download.assert_not_called()
        with patch.object(ci, "CANDIDATE", "P21"), patch.object(ci, "download") as download:
            ci.download_v8("https://example.invalid/historical", Path("inputs/test.sha256"), "old digest")
            download.assert_called_once_with("https://example.invalid/historical/test.sha256",
                                             Path("inputs/test.sha256"), "old digest")

    def test_p167_v8_rest_accepts_exact_manifest_and_rejects_invalid_stream_once(self):
        manifest = next(name for name in ci.P167_V8_ASSETS if name.endswith(".sha256"))
        data = "".join(f"{value}  {name}\n" for name, value in ci.P167_V8_FILES.items()).encode()
        class Response(io.BytesIO):
            url = "https://release-assets.githubusercontent.com/pinned"
        with tempfile.TemporaryDirectory() as directory, patch.object(ci, "CANDIDATE", "P167"):
            root = Path(directory)
            for label, response, error in (
                ("valid", Response(data), None),
                ("truncated", Response(data[:-1]), "byte count"),
                ("same_size_bad_digest", Response(b"x" + data[1:]), "SHA256"),
            ):
                dest = root / label / manifest
                with self.subTest(label=label), patch.object(ci.urllib.request, "urlopen", return_value=response) as http:
                    if error is None:
                        record = ci.download_v8(ci.P167_V8_RELEASE, dest, ci.P167_V8_MANIFEST_SHA)
                        self.assertEqual(record["sha256"], ci.P167_V8_MANIFEST_SHA)
                        self.assertEqual(dest.read_bytes(), data)
                    else:
                        with self.assertRaisesRegex(ValueError, error):
                            ci.download_v8(ci.P167_V8_RELEASE, dest, ci.P167_V8_MANIFEST_SHA)
                        self.assertFalse(dest.exists())
                    self.assertEqual(http.call_count, 1)
                    request = http.call_args.args[0]
                    self.assertEqual(request.get_header("Accept"), "application/octet-stream")
                    self.assertIsNone(request.get_header("Authorization"))
            dest = root / "http504" / manifest
            failure = HTTPError("https://api.github.com", 504, "Gateway Timeout", {}, None)
            with patch.object(ci.urllib.request, "urlopen", side_effect=failure) as http:
                with self.assertRaises(HTTPError):
                    ci.download_v8(ci.P167_V8_RELEASE, dest, ci.P167_V8_MANIFEST_SHA)
                self.assertEqual(http.call_count, 1)
                self.assertFalse(dest.exists())

    def test_completed_producer_cache_requires_exact_six_bytes_before_copy(self):
        resume = ci.load_module("p167_completed_cache_test", ci.REPO / "scripts/ci/p167-tests-resume.py")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve(strict=True)
            release = target / "release"
            release.mkdir()
            rows = []
            for name in sorted(ci.BINS):
                path = release / name
                path.write_bytes(name.encode())
                rows.append({"file": name, "size": path.stat().st_size, "sha256": ci.sha(path)})
            self.assertEqual(resume.cache_binaries(target, {"binaries": rows}, ci.BINS), rows)
            with self.assertRaisesRegex(ValueError, "Exact six"):
                resume.cache_binaries(target, {"binaries": rows[:-1]}, ci.BINS)
            sample = release / rows[0]["file"]
            sample.write_bytes(b"x" * rows[0]["size"])
            with self.assertRaisesRegex(ValueError, "Cached producer differs"):
                resume.cache_binaries(target, {"binaries": rows}, ci.BINS)
            sample.unlink()
            with self.assertRaisesRegex(ValueError, "Cached producer differs"):
                resume.cache_binaries(target, {"binaries": rows}, ci.BINS)
            self.assertFalse((target / "native-producer").exists())
        with patch.dict(os.environ, {"CI_P167_TEST_RESUME": "1"}):
            with self.assertRaisesRegex(ValueError, "must not compile the six"):
                ci.build(Path("unused"))
            with self.assertRaisesRegex(ValueError, "retains the exact original Git12"):
                ci.git_unit(Path("unused"))

    def test_core_warm_timeout_requires_exact_command_progress_and_no_survivors(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "warm.log"
            progress = '{"reason":"compiler-artifact","fresh":false}\n'
            log.write_text(progress)
            command = ci.unit_command(Path("toolchain"), 2, "codex-core")
            raw = {"command":command, "fixed_timeout_seconds":1800, "passed":False, "timed_out":True,
                   "returncode":1, "wrapper_minimum_available_memory_bytes":ci.FLOOR, "log_sha256":ci.sha(log)}
            self.assertEqual(ci.admit_warm_result(raw, command, log), "warm_compile_timed_out_without_survivors")
            for change in ({"command":["other"]}, {"fixed_timeout_seconds":3600}, {"unknown_live":[1]},
                           {"remaining_before_cleanup":[1]}, {"remaining_after_cleanup":[1]},
                           {"forced_cleanup":[1]}, {"resource_guard":{"minimum":1}}, {"timed_out":False}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    ci.admit_warm_result({**raw, **change}, command, log)
            for output in ('{"reason":"compiler-message","message":{"level":"error"}}\n',
                           '{"reason":"build-finished","success":false}\n', ""):
                log.write_text((progress if output else "")+output)
                with self.assertRaises(ValueError):
                    ci.admit_warm_result({**raw,"log_sha256":ci.sha(log)}, command, log)
            log.write_text(progress+'{"reason":"build-finished","success":true}\n')
            success = {**raw,"passed":True,"timed_out":False,"returncode":0,"log_sha256":ci.sha(log)}
            self.assertEqual(ci.admit_warm_result(success, command, log), "warm_compile_completed")

    def test_core_warm_records_natural_mtime_changes_even_when_bytes_match(self):
        builder = ci.load_module("p167_warm_mtime_builder", ci.REPO / "scripts/build_native.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            original = source / "fixture.rs"
            original.write_bytes(b"same bytes")
            v8file = root / "v8.lib.gz"
            v8file.write_bytes(b"same V8")
            v8 = {"files":[{"file":str(v8file),"size":v8file.stat().st_size,"sha256":ci.sha(v8file)}]}
            state = builder.source_state(source)
            lock = {"prepared_source_sha256":state["sha256"], "prepared_source_files":state["count"]}
            first = ci.warm_inputs(builder, source, v8, lock)
            time.sleep(.04)
            original.write_bytes(b"same bytes")
            second = ci.warm_inputs(builder, source, v8, lock)
            self.assertEqual(first["source_sha256"], second["source_sha256"])
            self.assertNotEqual(first["natural_source_mtimes_sha256"], second["natural_source_mtimes_sha256"])
            v8file.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Core V8 input differs"):
                ci.warm_inputs(builder, source, v8, lock)

    def test_only_core_uses_jobs4_and_admission_rejects_wrong_hardware(self):
        with patch.object(ci,"CANDIDATE","P167"), patch.dict(os.environ, {
            "CI_P167_CORE_JOBS4":"1","CI_P20_BUILD_JOBS":"2",
        }):
            self.assertEqual(ci.build_jobs(),2)
            self.assertEqual(ci.unit_jobs("codex-core"),4)
            for name in ("codex-guardian-v2","codex-core-plugins","codex-windows-sandbox"):
                self.assertEqual(ci.unit_jobs(name),2)
            self.assertEqual(ci.unit_command(Path("toolchain"),ci.unit_jobs("codex-core"),"codex-core")[4:6],
                             ["--jobs","4"])
        for cpus, available in ((4,8*1024**3),(2,8*1024**3),(4,8*1024**3-1)):
            fake = SimpleNamespace(cpu_count=lambda logical:cpus,
                                   virtual_memory=lambda:SimpleNamespace(total=17174360064,available=available))
            with patch.dict(sys.modules, {"psutil":fake}):
                if cpus==4 and available>=8*1024**3:
                    d=ci.core_resource_admission()
                    self.assertEqual((d["logical_cpus"],d["initial_available_memory_bytes"],d["memory_floor_bytes"]),
                                     (4,8*1024**3,4*1024**3))
                else:
                    with self.assertRaisesRegex(ValueError,"four logical CPUs"):
                        ci.core_resource_admission()

    def test_workflow_read_only_no_rebuild_and_exact_cache_restore(self):
        text = (ci.REPO / ".github/workflows/p20-native-windows.yml").read_text()
        self.assertIn("runs-on: windows-2022", text)
        self.assertIn("contents: read", text)
        self.assertIn("persist-credentials: false", text)
        resume = ci.load_module("p167_test_resume_tests", ci.REPO / "scripts/ci/p167-tests-resume.py")
        self.assertEqual(text.count("actions/cache/restore"), 1)
        self.assertIn(f"key: {resume.KEY}", text)
        self.assertIn("fail-on-cache-miss: true", text)
        self.assertNotIn("restore-keys:", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("p20-windows.py build --candidate P167", text)
        self.assertNotIn("p20-windows.py unit --candidate P167", text)
        self.assertEqual(text.count("p20-windows.py restore_producer --candidate P167"), 1)
        self.assertLess(text.index("p20-windows.py locked_fetch"), text.index("p20-windows.py restore_producer"))
        self.assertNotIn("p20-windows.py git_unit --candidate P167", text)
        self.assertLess(text.index("p20-windows.py restore_producer"), text.index("p20-windows.py warm_core"))
        self.assertLess(text.index("p20-windows.py warm_core"), text.index("p20-windows.py extra_unit"))
        self.assertEqual(text.count("p20-windows.py warm_core --candidate P167"), 1)
        self.assertIn("CI_P167_CORE_JOBS4: '1'", text)
        self.assertIn("CI_P20_BUILD_JOBS: '2'", text)
        self.assertTrue(all(re.fullmatch(r"actions/(?:[a-z-]+|cache/(?:restore|save))@[a-f0-9]{40}", action)
                            for action in re.findall(r"uses: (\S+)", text)))


if __name__ == "__main__":
    unittest.main()
