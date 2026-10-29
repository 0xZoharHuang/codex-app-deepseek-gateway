from __future__ import annotations
import copy,json
from pathlib import Path
import pytest
from codex_app_deepseek import isolated_app as app


@pytest.fixture
def generation(tmp_path,monkeypatch):
    paths={"home":tmp_path/"home","frontend":tmp_path/"home/frontend",
           "launches":tmp_path/"launches","native_logs":tmp_path/"logs"}
    paths["home"].mkdir();paths["launches"].mkdir()
    monkeypatch.setattr(app,"defaults",lambda environment=None:paths)
    run=paths["launches"]/("a"*32);run.mkdir()
    native=tmp_path/"native/codex.exe";native.parent.mkdir();native.write_bytes(b"fixture expected native never executed")
    app_exe=tmp_path/"package/app/ChatGPT.exe";app_exe.parent.mkdir(parents=True);app_exe.write_bytes(b"fixture App never executed")
    env={"CODEX_HOME":str(paths["home"]),"CODEX_CLI_PATH":str(native),
         "CODEX_ELECTRON_USER_DATA_PATH":str(paths["frontend"]),"CODEX_APP_SERVER_FORCE_CLI":"1"}
    row={"name":"codex.exe","path":str(native),"sha256":app.sha(native)}
    manifest=native.parent/"app-runtime-manifest.json";manifest.write_text(json.dumps({"outputs":[row]}))
    sid="S-1-5-21-fixture-1001"
    root={"pid":10,"created":100.0,"sid":sid,"session":1,"exe":str(app_exe),"ppid":1}
    old={**root,"pid":20,"created":101.0,"exe":str(native),"ppid":10}
    new={**root,"pid":21,"created":102.0,"exe":str(native),"ppid":10}
    plan={"request":{"native_manifest":str(manifest),"native_manifest_sha256":app.sha(manifest)},
          "validated":{"owner":{"sid":sid,"session":1},"environment":env,
                       "bundle":{"manifest":str(manifest),"manifest_sha256":app.sha(manifest),"files":{"codex.exe":row}}}}
    (run/"plan.json").write_text(json.dumps(plan))
    receipt={"status":"started_expected_backend","plan_sha256":app.sha(run/"plan.json"),"app":root,
             "app_executable":str(app_exe),"expected_environment":env,
             "native_processes":[{**old,"native_role":"app-server"}]}
    (run/"receipt.json").write_text(json.dumps(receipt))
    class Backend:
        def __init__(self):
            self.identities={10:copy.deepcopy(root),20:copy.deepcopy(old),21:copy.deepcopy(new)}
            self.environments={10:copy.deepcopy(env),21:copy.deepcopy(env)}
            self.roles={21:"app-server"};self.dead={20};self.descendant_pids=[10,21]
            self.current_value={"sid":sid,"session":0}
            self.descendant_calls=0
        def current(self):return dict(self.current_value)
        def live(self,item):
            actual=self.identities.get(item["pid"])
            return bool(actual and item["pid"] not in self.dead and actual["created"]==item["created"])
        def identity(self,pid):return copy.deepcopy(self.identities[pid])
        def selected_environment(self,item):return copy.deepcopy(self.environments[item["pid"]])
        def native_role(self,item):return self.roles[item["pid"]]
        def descendants(self,item):
            self.descendant_calls+=1
            return [self.identity(pid) for pid in self.descendant_pids]
        def native_control_plane(self, item, *, executable, expected_sha256, environment):
            assert Path(executable) == native and expected_sha256 == row["sha256"]
            assert environment == env
            return self.descendants(item)
    return run,native,manifest,receipt,Backend()


def test_old_backend_dead_new_generation_is_verified_without_rewriting_receipt_or_session_switch(generation):
    run,native,manifest,receipt,backend=generation
    original=(run/"receipt.json").read_bytes()
    result=app.launch_status(run,backend)
    assert [v["live"] for v in result["processes"]]==[True,False]
    assert result["receipt"]==receipt and (run/"receipt.json").read_bytes()==original
    observed=result["current_backend_observation"]
    assert observed["status"]=="verified_current_backends"
    assert [v["pid"] for v in observed["native_processes"]]==[21]
    assert observed["native_processes"][0]["observed_executable_sha256"]==app.sha(native)
    assert [v["pid"] for v in observed["native_processes"][0]["ancestor_chain"]]==[21,10]
    assert observed["original_receipt_sha256"]==app.sha(run/"receipt.json")
    assert backend.current()["session"]==0 and not observed["app_runtime_acceptance"]


@pytest.mark.parametrize("mismatch",["environment","image_hash","parent","owner","session","birth","App_PID_reused","App_dead","manifest_hash","plan_hash"])
def test_unknown_generation_never_becomes_a_verified_backend(generation,mismatch):
    run,native,manifest,receipt,backend=generation
    if mismatch=="environment":backend.environments[21]["CODEX_HOME"]="foreign-home"
    elif mismatch=="image_hash":native.write_bytes(b"same path replaced with different image")
    elif mismatch=="parent":
        backend.identities[99]={**backend.identities[10],"pid":99,"created":99.0,"ppid":1}
        backend.identities[21]["ppid"]=99
    elif mismatch=="owner":backend.identities[21]["sid"]="S-1-5-21-other"
    elif mismatch=="session":backend.identities[21]["session"]=2
    elif mismatch=="birth":backend.identities[21]["created"]=99.0
    elif mismatch=="App_PID_reused":backend.identities[10]["created"]=200.0
    elif mismatch=="App_dead":backend.dead.add(10)
    elif mismatch=="manifest_hash":manifest.write_bytes(manifest.read_bytes()+b"\n")
    else:(run/"plan.json").write_bytes((run/"plan.json").read_bytes()+b"\n")
    original=(run/"receipt.json").read_bytes()
    result=app.launch_status(run,backend)
    assert result["current_backend_observation"]["status"]=="unknown"
    assert not result["current_backend_observation"]["app_runtime_acceptance"]
    assert (run/"receipt.json").read_bytes()==original
    if mismatch in {"App_PID_reused","App_dead"}:assert backend.descendant_calls==0


def test_help_or_schema_process_is_excluded_not_promoted_to_backend(generation):
    run,_,_,_,backend=generation
    backend.roles[21]="other-native-command"
    result=app.launch_status(run,backend)["current_backend_observation"]
    assert result["status"]=="no_current_backend_verified" and result["native_processes"]==[]
    assert result["excluded_native_processes"]==[{"pid":21,"created":102.0,"native_role":"other-native-command"}]


def test_no_current_generation_does_not_claim_backend_ready(generation):
    run,_,_,_,backend=generation
    backend.descendant_pids=[10]
    result=app.launch_status(run,backend)["current_backend_observation"]
    assert result["status"]=="no_current_backend_verified" and result["native_processes"]==[]


def test_unknown_native_role_stays_unknown(generation):
    run,_,_,_,backend=generation
    backend.roles[21]="exited-native-command"
    result=app.launch_status(run,backend)["current_backend_observation"]
    assert result["status"]=="unknown"


def test_status_uses_bound_control_plane_not_runtime_workload_tree(generation):
    run,native,_,_,backend=generation;calls=[]
    def observe(root,**binding):
        calls.append(binding)
        return [backend.identity(10),backend.identity(21)]
    backend.native_control_plane=observe
    def broad(root):raise app.LaunchError("unexpected owner/session in App process tree")
    backend.descendants=broad
    result=app.launch_status(run,backend)["current_backend_observation"]
    assert result["status"]=="verified_current_backends"
    assert result["validation_scope"]=="app_frontend_and_verified_native_backends"
    assert result["native_workload_scope"]=="owned_by_native_runtime_not_enumerated"
    assert calls[0]["expected_sha256"]==app.sha(native)
