"""Offline admission and receipt checks. This suite never starts Cargo or an App."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
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
        ):
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
        ):
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

    def test_workflow_read_only_one_cold_build_and_no_restore(self):
        text = (ci.REPO / ".github/workflows/p20-native-windows.yml").read_text()
        self.assertIn("runs-on: windows-2022", text)
        self.assertIn("contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("actions/cache/restore", text)
        self.assertNotIn("secrets.", text)
        self.assertEqual(text.count("p20-windows.py build --candidate P167"), 1)
        self.assertTrue(all(re.fullmatch(r"actions/(?:[a-z-]+|cache/save)@[a-f0-9]{40}", action)
                            for action in re.findall(r"uses: (\S+)", text)))


if __name__ == "__main__":
    unittest.main()
