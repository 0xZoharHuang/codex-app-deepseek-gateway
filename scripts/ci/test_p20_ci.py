"""Offline CI safety/configuration checks; never build or reclaim hosted tools."""
from pathlib import Path
import hashlib, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, unittest
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
        uses=re.findall(r"uses:\s+(\S+)",text);self.assertEqual(len(uses),5)
        self.assertTrue(all(re.fullmatch(r"actions/(?:[a-z-]+|cache/(?:restore|save))@[0-9a-f]{40}",x)for x in uses))
    def test_parallelism_and_stable_performance_cache(self):
        self.assertEqual(ci.build_jobs({}),2)
        self.assertEqual(ci.build_jobs({"CI_P20_BUILD_JOBS":"1"}),1)
        for value in ("0","3","auto",""):
            with self.subTest(value=value),self.assertRaises(ValueError):
                ci.build_jobs({"CI_P20_BUILD_JOBS":value})
        first=ci.cache_paths(Path("C:/cagp20-123-1"))
        self.assertEqual(first,ci.cache_paths(Path("C:/cagp20-987-2")))
        self.assertEqual(first[1],first[0]/"target")
        text=(ci.REPO/".github/workflows/p20-native-windows.yml").read_text()
        self.assertIn("actions/cache/restore@",text);self.assertIn("actions/cache/save@",text)
        self.assertIn("always() && !cancelled()",text)
        self.assertIn("CI_P20_BUILD_JOBS: '2'",text)
    def test_normal_raw_schema_is_binaries_and_not_prepared_outputs(self):
        import hashlib,copy
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);(root/"native-producer").mkdir()
            rows=[]
            for name in sorted(ci.BINS):
                raw=("synthetic-"+name).encode();(root/"native-producer"/name).write_bytes(raw)
                rows.append({"file":name,"sha256":hashlib.sha256(raw).hexdigest()})
            good={"source_sha256":ci.SOURCE,"build_lock_sha256":ci.LOCK,
                  "status":"built-not-app-qualified","binaries":rows}
            ci.verify_normal_outputs(root,good)
            for change in ("prepared","missing","hash","source"):
                bad=copy.deepcopy(good)
                if change=="prepared":bad["outputs"]=bad.pop("binaries")
                elif change=="missing":bad["binaries"].pop()
                elif change=="hash":bad["binaries"][0]["sha256"]="0"*64
                else:bad["source_sha256"]="0"*64
                with self.subTest(change=change),self.assertRaises((ValueError,KeyError)):
                    ci.verify_normal_outputs(root,bad)
    def test_only_exact_retired_msvc_telemetry_is_classified(self):
        import copy
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            files={n:folder/n for n in ("cl.exe","link.exe","vctip.exe")}
            for path in files.values(): path.write_bytes(b"synthetic fixture")
            toolchain={"compiler":str(files["cl.exe"]),"linker":str(files["link.exe"])}
            parent={"pid":10,"birth":"windows:100","stable":True,"exe":str(files["cl.exe"])}
            tip={"pid":20,"birth":"windows:200","birth_after":"windows:200","stable":True,
                 "ownership":"stable_direct_parent_edge","parent_pid":10,"ppid":10,
                 "parent_birth":"windows:100","exe":str(files["vctip.exe"]),"argv":[str(files["vctip.exe"])]}
            good={"returncode":0,"timed_out":False,"remaining_before_cleanup":[tip],"forced_cleanup":[tip],
                  "observed":[parent,tip],"remaining_after_cleanup":[],"unknown_live":[]}
            self.assertEqual(ci.classify_compiler_telemetry_cleanup(good,toolchain)["birth"],tip["birth"])
            for changes in ({"timed_out":True},{"returncode":1},{"unknown_live":[20]},
                            {"remaining_after_cleanup":[tip]},{"forced_cleanup":[]},{"observation_errors":["error"]},
                            {"cleanup_errors":["error"]},{"wrapper_resource_guard":{"floor":4}},
                            {"remaining_before_cleanup":[tip,tip]},{"observed":[tip]}):
                with self.subTest(changes=changes):
                    self.assertIsNone(ci.classify_compiler_telemetry_cleanup({**good,**changes},toolchain))
            for key,value in (("parent_birth","windows:99"),("birth_after","windows:201"),
                              ("exe",str(folder/"other/vctip.exe")),("argv",[str(files["vctip.exe"]),"foreign"])):
                bad=copy.deepcopy(good)
                bad["remaining_before_cleanup"][0][key]=value
                with self.subTest(key=key):
                    self.assertIsNone(ci.classify_compiler_telemetry_cleanup(bad,toolchain))
    def test_third_party_build_and_unit_environment_excludes_action_and_model_credentials(self):
        supplied={"PATH":"keep","CARGO_HOME":"keep-cargo","RUSTY_V8_ARCHIVE":"keep-v8",
                  "ACTIONS_RUNTIME_TOKEN":"dummy","GITHUB_TOKEN":"dummy","GH_TOKEN":"dummy",
                  "OPENROUTER_API_KEY":"dummy","OPENAI_API_KEY":"dummy","ANTHROPIC_API_KEY":"dummy"}
        result=ci.clean_environment(supplied)
        self.assertEqual(result["PATH"],"keep")
        self.assertEqual(result["CARGO_HOME"],"keep-cargo")
        self.assertEqual(result["RUSTY_V8_ARCHIVE"],"keep-v8")
        self.assertFalse(any(k.startswith(("ACTIONS_","GITHUB_","GH_","OPENAI_","OPENROUTER_","ANTHROPIC_"))for k in result))
    def test_real_git_autocrlf_checkout_preserves_pinned_blob_bytes(self):
        git=shutil.which("git.exe")or shutil.which("git")
        if git is None and os.name=="nt":
            candidate=Path(os.environ.get("ProgramFiles",r"C:\Program Files"))/"Git/cmd/git.exe"
            if candidate.is_file():git=str(candidate)
        self.assertIsNotNone(git,"Real Git is required for byte roundtrip admission")
        with tempfile.TemporaryDirectory(prefix="p20-git-byte-roundtrip-")as temporary:
            temp=Path(temporary);empty=temp/"empty";empty.mkdir()
            global_config=temp/"empty.gitconfig";global_config.write_bytes(b"")
            env=ci.clean_environment()
            for key in list(env):
                if key.startswith("GIT_"):env.pop(key)
            env.update(GIT_CONFIG_NOSYSTEM="1",GIT_CONFIG_GLOBAL=str(global_config),
                       GIT_ATTR_NOSYSTEM="1",XDG_CONFIG_HOME=str(empty))
            def run(root,*args,data=None):
                result=subprocess.run([git,"-c",f"core.hooksPath={empty}","-C",str(root),*args],
                                      input=data,capture_output=True,env=env)
                self.assertEqual(result.returncode,0,result.stderr.decode("utf8",errors="replace"))
                return result.stdout
            names=run(ci.REPO,"ls-files","-z","--","native","scripts/build_native.py",
                      "scripts/package_native_release.py","scripts/ci").decode().split("\0")
            names=[n for n in names if n and(n.endswith(".patch")or
                   re.fullmatch(r"native/build-lock\..*-source\.json",n)or
                   n in("scripts/build_native.py","scripts/package_native_release.py")or
                   n.startswith("scripts/ci/"))]
            license_data=json.loads((ci.REPO/"native/license-data.json").read_bytes())
            spdx={f"native/licenses/spdx/{row['id']}.txt":row["sha256"]
                  for row in license_data["texts"]}
            self.assertTrue(spdx)
            self.assertEqual(set(spdx),{p.relative_to(ci.REPO).as_posix()
                                       for p in (ci.REPO/"native/licenses/spdx").glob("*.txt")})
            names.extend(sorted(spdx))
            self.assertIn("native/build-lock.O165-P20-source.json",names)
            source=run(ci.REPO,"cat-file","--batch",
                       data="".join("HEAD:"+n+"\n"for n in names).encode())
            blobs={};cursor=0
            for name in names:
                end=source.index(b"\n",cursor);header=source[cursor:end].split()
                self.assertEqual(header[1],b"blob")
                size=int(header[2]);cursor=end+1
                blobs[name]=source[cursor:cursor+size];cursor+=size+1
            for name, expected in spdx.items():
                self.assertEqual(hashlib.sha256(blobs[name]).hexdigest(),expected,name)
            def checkout(label,attributes):
                repo=temp/label;repo.mkdir()
                run(repo,"init","--quiet","--object-format=sha1",f"--template={empty}")
                run(repo,"config","core.autocrlf","true")
                run(repo,"config","core.safecrlf","false")
                inputs={".gitattributes":attributes,**blobs}
                for name,data in inputs.items():
                    path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
                object_names=run(repo,"hash-object","-w","--no-filters","--stdin-paths",
                                 data="".join(n+"\n"for n in inputs).encode()).decode().splitlines()
                self.assertEqual(len(object_names),len(inputs))
                entries="".join("100644 "+oid+"\t"+n+"\n"for n,oid in zip(inputs,object_names))
                run(repo,"update-index","--add","--index-info",data=entries.encode())
                output=temp/(label+"-checkout");output.mkdir()
                run(repo,"checkout-index","--all","--force","--prefix="+output.as_posix()+"/")
                return [name for name,data in blobs.items()if(output/name).read_bytes()!=data]
            historical=(ci.REPO/"scripts/ci/pre-p20-byte-pins.gitattributes").read_bytes()
            old_differences=checkout("historical",historical)
            self.assertIn("native/build-lock.O165-P20-source.json",old_differences)
            self.assertIn("scripts/build_native.py",old_differences)
            self.assertIn("scripts/package_native_release.py",old_differences)
            self.assertTrue(any(x.startswith("scripts/ci/")for x in old_differences))
            self.assertTrue(set(spdx).issubset(old_differences))
            self.assertEqual(checkout("fixed",(ci.REPO/".gitattributes").read_bytes()),[])
    def test_normal_builder_and_packager_not_changed(self):
        self.assertEqual(ci.sha(ci.REPO/"scripts/build_native.py"),"f6426bc41f57304f4c252267bac5d97749912e20b4033b60a6a8a9613e18d0d4")
        self.assertEqual(ci.sha(ci.REPO/"scripts/package_native_release.py"),"3ea29a1e23a66fc3aded1c24d4723853a0e238f15871cd84c544ef13797e399e")
if __name__=="__main__":unittest.main()
