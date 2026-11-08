"""Offline early draft admission. No remote calls or native process launch."""
import contextlib
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

p = Path(__file__).with_name("p167-early-draft.py")
spec = importlib.util.spec_from_file_location("p167_early_draft_test", p)
early = importlib.util.module_from_spec(spec)
spec.loader.exec_module(early)

class EarlyDraft(unittest.TestCase):
    head = "a" * 40

    def state(self, assets=()):
        return {"id":17, "draft":True, "prerelease":True, "target_commitish":self.head,
                "published_at":None, "tag_name":"cadsg-p167-early-unqualified-"+self.head[:12],
                "name":"CADSG P167 EARLY_UNQUALIFIED", "assets":list(assets)}

    def test_published_wrong_target_wrong_label_or_existing_asset_rejected(self):
        for change in ({"draft":False}, {"prerelease":False}, {"target_commitish":"wrong"},
                       {"published_at":"time"}, {"tag_name":"other"}, {"name":"qualified"},
                       {"assets":[{"name":"old.tar.gz"}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                early.draft_state({**self.state(),**change},17,self.head,{})
        early.draft_state(self.state(),17,self.head,{})

    def test_upload_uses_only_exact_draft_and_keeps_payload_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/"evidence").mkdir()
            archive = root/"assembled.tar.gz"; archive.write_bytes(b"fixture archive")
            digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            class CI:
                sha = staticmethod(digest)
                load = staticmethod(lambda p:json.loads(p.read_bytes()))
            proof = {"status":"P167_EARLY_UNQUALIFIED_PRIVATE_SMOKE_ONLY","head":self.head,"run":"21",
                     "archive_sha256":digest(archive),"archive_size":archive.stat().st_size,
                     "full_native_suite_qualified":False,"App_GUI_qualified":False,"public_stable_release":False}
            receipt = root/"evidence/EARLY_UNQUALIFIED.json"
            receipt.write_text(json.dumps(proof))
            with patch.dict(os.environ, {"GH_TOKEN":"synthetic","GITHUB_RUN_ID":"21"}):
                with patch.object(early,"gh_json",return_value={**self.state(),"draft":False}) as api:
                    with self.assertRaises(ValueError):
                        early.upload(CI,root,self.head,17)
                    self.assertEqual(api.call_count,1)
                uploaded = []
                def api(*args):
                    if args[0] != "--method":
                        self.assertEqual(args,(f"repos/{early.REPOSITORY}/releases/17",))
                        return self.state(uploaded)
                    self.assertEqual(args[:2],("--method","POST"))
                    self.assertTrue(args[2].startswith(f"https://uploads.github.com/repos/{early.REPOSITORY}/releases/17/assets?"))
                    self.assertIn("label=EARLY_UNQUALIFIED",args[2])
                    source = Path(args[-1])
                    name = args[2].split("name=",1)[1].split("&",1)[0]
                    row = {"id":100+len(uploaded),"name":name,"size":source.stat().st_size,
                           "digest":"sha256:"+digest(source)}
                    uploaded.append(row)
                    return row
                with patch.object(early,"gh_json",side_effect=api), patch.object(early,"anonymous_hidden") as hidden:
                    early.upload(CI,root,self.head,17)
                    self.assertEqual([x.args[0] for x in hidden.call_args_list],[100,101])
                self.assertEqual(len(uploaded),2)

    def test_packaging_failure_exposes_bounded_sanitized_raw_diagnostics_and_stays_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); evidence = root / "evidence"
            (evidence / "early-assembly").mkdir(parents=True)
            (evidence / "early-normal-package.log").write_bytes(
                b"x"*8000+b"\nNative assembly rejected: locked source error\n"
                b"Authorization: Bearer ghp_demo_sensitive\nGITHUB_TOKEN=demo_sensitive\n")
            (evidence / "early-assembly/cargo-metadata.stderr").write_bytes(
                b"error: offline package missing\nhttps://server.invalid/asset?sig=demo_sensitive\n")
            (evidence / "early-normal-package.json").write_text(json.dumps({
                "passed":False,"returncode":1,"timed_out":False,"seconds":30,
                "unknown_live":[12],"observed":[{"argv":["demo_sensitive"]}]}))
            class CI:
                sha = staticmethod(lambda p:hashlib.sha256(p.read_bytes()).hexdigest())
                load = staticmethod(lambda p:json.loads(p.read_bytes()))
            out = io.StringIO()
            with patch.object(sys,"argv",["early","assemble","--root",str(root)]), \
                 patch.object(early,"controller",return_value=CI), \
                 patch.object(early,"admitted_context",return_value=(root,self.head)), \
                 patch.object(early,"assemble",side_effect=ValueError("original package failure")) as run, \
                 patch.object(early,"upload") as upload, contextlib.redirect_stdout(out):
                with self.assertRaisesRegex(ValueError,"original package failure"):
                    early.main()
                run.assert_called_once()
                upload.assert_not_called()
            text=out.getvalue()
            self.assertNotIn("demo_sensitive",text)
            self.assertEqual(len(text.splitlines()),1)
            payload=json.loads(text.removeprefix("EARLY_UNQUALIFIED_DIAGNOSTIC "))
            self.assertTrue(payload["packager_log"]["truncated"])
            self.assertLessEqual(len(payload["packager_log"]["tail"]),4096)
            self.assertIn("Native assembly rejected:",payload["packager_log"]["tail"])
            self.assertIn("offline package missing",payload["cargo_metadata_stderr"]["tail"])
            self.assertEqual(payload["observer"]["unknown_live_count"],1)
            self.assertEqual(payload["observer"]["returncode"],1)
            self.assertNotIn("observed",payload["observer"])

    def test_workflow_has_only_private_upload_no_native_compile_or_artifact(self):
        path=early.REPO/".github/workflows/p20-native-package-resume.yml"
        text=path.read_text()
        for forbidden in ("actions/upload-artifact", "actions/cache/save", "p20-windows.py build ",
                          "p20-windows.py unit ", "p20-windows.py git_unit ", "p20-windows.py extra_unit "):
            self.assertNotIn(forbidden,text)
        self.assertIn("p20-windows.py restore_producer --candidate P167",text)
        self.assertIn("p167-early-draft.py upload",text)
        self.assertIn("fail-on-cache-miss: true",text)
        with patch.dict(os.environ, {"CI_P167_TEST_RESUME":"1"}):
            resume = early.controller().resume_controller()
            self.assertIn(f"key: {resume.KEY}",text)

if __name__ == "__main__":
    unittest.main()
