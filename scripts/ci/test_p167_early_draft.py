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

    def retained(self, resume):
        return {"same_runner_core_run":resume.RUN, "same_runner_core_artifact_sha256":resume.ZIP_SHA,
                "same_runner_core_compile_sha256":resume.CORE_SAME_RUNNER_SHA, "repeat_core_compile_run":"42511450429",
                "repeat_core_artifact_sha256":"5c504baf7ae12bcf96073ff6b12567d15612110ceb2ae5a2daa38e0a854eb213",
                "repeat_core_compile_sha256":resume.CORE_REPEAT_SHA, "raw_manifest_sha256":early.ORIGINAL_RAW,
                "native_producer_rerun":False, "original_sandbox_reexecuted":False, "original_git_reexecuted":False,
                "cache_key":resume.EARLY_KEY, "failed_formal_cache":{
                    **resume.formal_identity(), "records":dict.fromkeys(resume.FORMAL_RECORDS, "a"*64)}}

    def test_upload_uses_only_exact_draft_and_keeps_payload_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/"evidence").mkdir()
            archive = root/"assembled.tar.gz"; archive.write_bytes(b"fixture archive")
            digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.dict(os.environ, {"CI_P167_TEST_RESUME":"1"}):
                resume = early.controller().resume_controller()
            class CI:
                sha = staticmethod(digest)
                load = staticmethod(lambda p:json.loads(p.read_bytes()))
                resume_controller = staticmethod(lambda:resume)
            retained = self.retained(resume)
            (root/"evidence/p167-retained-producer.json").write_text(json.dumps(retained))
            assembly_path = root / "evidence/early-assembly/assembly-result.json"
            assembly_path.parent.mkdir()
            assembly = {"archive":str(archive), "kind":"clean_source_assembly",
                        "status":"raw_native_assembled_not_App_qualified", "raw_binary_count":6,
                        "no_compile_or_native_execution":True, "original_source_and_receipts_unchanged":True,
                        "manifest_sha256":"a"*64,
                        "archive_identity":{"sha256":digest(archive),"size":archive.stat().st_size}}
            raw = (json.dumps(assembly, indent=2) + "\n").encode()
            assembly_path.write_bytes(raw)
            proof = {"status":"P167_EARLY_UNQUALIFIED_PRIVATE_SMOKE_ONLY","head":self.head,"run":"21",
                     "archive_sha256":digest(archive),"archive_size":archive.stat().st_size,
                     "normal_assembly_receipt_asset":early.ASSEMBLY_ASSET,
                     "normal_assembly_receipt_sha256":digest(assembly_path),
                     "normal_assembly_receipt_size":assembly_path.stat().st_size,
                     "native_manifest_sha256":assembly["manifest_sha256"],
                     "retained_producer":retained,
                     "full_native_suite_qualified":False,"App_GUI_qualified":False,"public_stable_release":False}
            receipt = root/"evidence/EARLY_UNQUALIFIED.json"
            receipt.write_text(json.dumps(proof))
            with patch.dict(os.environ, {"GH_TOKEN":"synthetic","GITHUB_RUN_ID":"21"}):
                with patch.object(early,"gh_json") as api:
                    assembly_path.write_bytes(raw + b" ")
                    with self.assertRaisesRegex(ValueError,"Exact original same-run assembly receipt required"):
                        early.upload(CI,root,self.head,17)
                    wrong = json.dumps({**assembly, "manifest_sha256":"b"*64}).encode()
                    assembly_path.write_bytes(wrong)
                    receipt.write_text(json.dumps({**proof,"normal_assembly_receipt_sha256":digest(assembly_path),
                                                   "normal_assembly_receipt_size":len(wrong)}))
                    with self.assertRaisesRegex(ValueError,"receipt differs from archive or manifest"):
                        early.upload(CI,root,self.head,17)
                    api.assert_not_called()
                assembly_path.write_bytes(raw)
                receipt.write_text(json.dumps(proof))
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
                    if name == early.ASSEMBLY_ASSET:
                        self.assertEqual(source,assembly_path)
                        self.assertEqual(source.read_bytes(),raw)
                    row = {"id":100+len(uploaded),"name":name,"size":source.stat().st_size,
                           "digest":"sha256:"+digest(source)}
                    uploaded.append(row)
                    return row
                with patch.object(early,"gh_json",side_effect=api), patch.object(early,"anonymous_hidden") as hidden:
                    early.upload(CI,root,self.head,17)
                    self.assertEqual([x.args[0] for x in hidden.call_args_list],[100,101,102])
                self.assertEqual(len(uploaded),3)
                self.assertEqual(uploaded[1],{"id":101,"name":early.ASSEMBLY_ASSET,
                    "size":len(raw),"digest":"sha256:"+hashlib.sha256(raw).hexdigest()})

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

    def test_retained_chain_binds_latest_same_runner_and_previous_repeat(self):
        with patch.dict(os.environ, {"CI_P167_TEST_RESUME":"1"}):
            resume = early.controller().resume_controller()
        retained = self.retained(resume)
        early.retained_identity(retained,resume)
        early.require_failed_formal(retained,resume)
        for changed in ({"test_cases_credited":3}, {"conclusion":"success"}, {"cache_id":1},
                        {"early_packager_sha256":resume.FORMAL_PACKAGER}, {"records":{}}):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError,"zero test credit"):
                early.require_failed_formal({**retained, "failed_formal_cache":{
                    **retained["failed_formal_cache"], **changed}}, resume)
        with self.assertRaisesRegex(ValueError,"zero test credit"):
            early.require_failed_formal({**retained, "cache_key":resume.KEY}, resume)
        for changed in ({"same_runner_core_run":retained["repeat_core_compile_run"]},
                        {"same_runner_core_artifact_sha256":retained["repeat_core_artifact_sha256"]},
                        {"repeat_core_compile_run":resume.RUN},{"repeat_core_artifact_sha256":resume.ZIP_SHA},
                        {"raw_manifest_sha256":"different"},{"original_git_reexecuted":True}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                early.retained_identity({**retained,**changed},resume)

    def test_exact_failed_run_artifact_and_cache_metadata(self):
        with patch.dict(os.environ, {"CI_P167_TEST_RESUME":"1"}):
            resume = early.controller().resume_controller()
        branch = resume.EARLY_REF.removeprefix("refs/heads/")
        run = {"id":resume.EARLY_RUN, "head_sha":resume.EARLY_HEAD, "head_branch":branch,
               "status":"completed", "conclusion":"failure", "run_attempt":1,
               "path":".github/workflows/p20-native-windows.yml", "event":"workflow_dispatch"}
        job = {"id":resume.EARLY_JOB, "run_id":resume.EARLY_RUN, "head_sha":resume.EARLY_HEAD,
               "status":"completed", "conclusion":"failure",
               "steps":[{"name":n, "conclusion":s} for n,s in resume.FORMAL_STEPS.items()]}
        artifact = {"id":resume.EARLY_ARTIFACT, "name":f"p167-native-windows-{resume.EARLY_RUN}-1",
                    "size_in_bytes":resume.EARLY_ZIP_SIZE, "expired":False, "digest":"sha256:"+resume.EARLY_ZIP_SHA,
                    "workflow_run":{"id":resume.EARLY_RUN, "head_sha":resume.EARLY_HEAD, "head_branch":branch,
                                    "repository_id":1442389092, "head_repository_id":1442389092}}
        cache = {"id":resume.EARLY_CACHE_ID, "key":resume.EARLY_KEY,
                 "version":resume.EARLY_CACHE_VERSION, "ref":resume.EARLY_REF}
        caches = {"total_count":1, "actions_caches":[cache]}
        resume.verify_formal_metadata(run,job,artifact,caches)
        for values in (({**run,"conclusion":"success"},job,artifact,caches),
                       (run,{**job,"steps":job["steps"][:-1]},artifact,caches),
                       (run,job,{**artifact,"expired":True},caches),
                       (run,job,artifact,{**caches,"actions_caches":[{**cache,"key":resume.KEY}]})):
            with self.assertRaises(ValueError):
                resume.verify_formal_metadata(*values)

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
            self.assertIn(f"key: {resume.EARLY_KEY}",text)
            self.assertNotIn(resume.KEY,text)
            formal = (early.REPO/".github/workflows/p20-native-windows.yml").read_text()
            self.assertIn(f"key: {resume.KEY}",formal)
            self.assertNotIn(resume.EARLY_KEY,formal)

if __name__ == "__main__":
    unittest.main()
