"""Offline early draft admission. No remote calls or native process launch."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
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

    def test_workflow_has_only_private_upload_no_native_compile_or_artifact(self):
        path=early.REPO/".github/workflows/p20-native-package-resume.yml"
        text=path.read_text()
        for forbidden in ("actions/upload-artifact", "actions/cache/save", "p20-windows.py build ",
                          "p20-windows.py unit ", "p20-windows.py git_unit ", "p20-windows.py extra_unit "):
            self.assertNotIn(forbidden,text)
        self.assertIn("p20-windows.py restore_producer --candidate P167",text)
        self.assertIn("p167-early-draft.py upload",text)
        self.assertIn("fail-on-cache-miss: true",text)

if __name__ == "__main__":
    unittest.main()
