"""Offline CI safety/configuration checks; never build or reclaim hosted tools."""
from pathlib import Path
import importlib.util, os, re, subprocess, sys, tempfile, unittest
SCRIPT=Path(__file__).with_name("p20-windows.py")
spec=importlib.util.spec_from_file_location("p20_ci_test_target",SCRIPT)
ci=importlib.util.module_from_spec(spec);spec.loader.exec_module(ci)
class Safety(unittest.TestCase):
    def context(self):
        return {"GITHUB_ACTIONS":"true","RUNNER_ENVIRONMENT":"github-hosted",
                "RUNNER_OS":"Windows","RUNNER_ARCH":"X64","CI_REPOSITORY_VISIBILITY":"public",
                "GITHUB_RUN_ID":"123","GITHUB_RUN_ATTEMPT":"1",
                "GITHUB_REF":"refs/heads/codex/p20-hosted-ci-test"}
    def test_exact_source_profile_targets_and_component_contract(self):
        lock,builder,cases=ci.plan()
        self.assertEqual(lock["prepared_source_sha256"],ci.SOURCE)
        self.assertEqual(len(builder.build_commands(Path("toolchain"),lock["binaries"],jobs=1,runtime_package=True)),1)
        self.assertEqual(len(cases),18)
    def test_hosted_guard_rejects_each_wrong_surface(self):
        self.assertEqual(ci.hosted_context(self.context(),"nt"),("123","1"))
        for key in self.context():
            bad=self.context();bad[key]="wrong"
            with self.subTest(key=key),self.assertRaises(ValueError):ci.hosted_context(bad,"nt")
        with self.assertRaises(ValueError):ci.hosted_context(self.context(),"posix")
    def test_local_prepare_cannot_create_root(self):
        env=dict(os.environ);env.pop("GITHUB_ACTIONS",None)
        with tempfile.TemporaryDirectory()as d:
            target=Path(d)/"must-not-exist"
            run=subprocess.run([sys.executable,"-I","-B",str(SCRIPT),"prepare","--root",str(target)],env=env,capture_output=True,text=True)
            self.assertNotEqual(run.returncode,0);self.assertFalse(target.exists())
            self.assertIn("GitHub-hosted",run.stderr)
    def test_checksum_traversal_duplicate_and_bad_digest_rejected(self):
        digest="a"*64
        for data in [f"{digest} ../escape\n",f"{digest} file\n{digest} file\n","not-a-hash file\n"]:
            with self.subTest(data=data),self.assertRaises(ValueError):ci.checksum_lines(data.encode())
    def test_workflow_read_permission_standard_runner_and_pinned_official_actions(self):
        text=(ci.REPO/".github/workflows/p20-native-windows.yml").read_text()
        self.assertIn("runs-on: windows-2022",text);self.assertIn("contents: read",text)
        self.assertNotIn("secrets.",text);self.assertNotIn("id-token:",text)
        uses=re.findall(r"uses:\s+(\S+)",text);self.assertEqual(len(uses),3)
        self.assertTrue(all(re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}",x)for x in uses))
    def test_third_party_build_and_unit_environment_excludes_action_and_model_credentials(self):
        supplied={"PATH":"keep","CARGO_HOME":"keep-cargo","RUSTY_V8_ARCHIVE":"keep-v8",
                  "ACTIONS_RUNTIME_TOKEN":"dummy","GITHUB_TOKEN":"dummy","GH_TOKEN":"dummy",
                  "OPENROUTER_API_KEY":"dummy","OPENAI_API_KEY":"dummy","ANTHROPIC_API_KEY":"dummy"}
        result=ci.clean_environment(supplied)
        self.assertEqual(result["PATH"],"keep")
        self.assertEqual(result["CARGO_HOME"],"keep-cargo")
        self.assertEqual(result["RUSTY_V8_ARCHIVE"],"keep-v8")
        self.assertFalse(any(k.startswith(("ACTIONS_","GITHUB_","GH_","OPENAI_","OPENROUTER_","ANTHROPIC_"))for k in result))
    def test_normal_builder_and_packager_not_changed(self):
        self.assertEqual(ci.sha(ci.REPO/"scripts/build_native.py"),"f6426bc41f57304f4c252267bac5d97749912e20b4033b60a6a8a9613e18d0d4")
        self.assertEqual(ci.sha(ci.REPO/"scripts/package_native_release.py"),"3ea29a1e23a66fc3aded1c24d4723853a0e238f15871cd84c544ef13797e399e")
if __name__=="__main__":unittest.main()
