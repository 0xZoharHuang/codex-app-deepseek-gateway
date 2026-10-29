"""Adversarial process-boundary tests; no App, native program, or model starts."""
import copy
from pathlib import Path
import pytest
from codex_app_deepseek import isolated_app as app
from test_app_process_edges import Graph

def configured(monkeypatch, tmp_path):
    graph=Graph(monkeypatch)
    native=tmp_path/"native/codex.exe";native.parent.mkdir()
    native.write_bytes(b"immutable expected native fixture; never executed")
    graph.rows[30]["exe"]=str(native)
    graph.arguments[30]=[str(native),"-c","feature.note=generate-ts","app-server","--analytics-default-enabled"]
    env={"CODEX_HOME":str(tmp_path/"home"),"CODEX_CLI_PATH":str(native),
         "CODEX_ELECTRON_USER_DATA_PATH":str(tmp_path/"home/frontend"),"CODEX_APP_SERVER_FORCE_CLI":"1"}
    graph.environments={10:copy.deepcopy(env),30:copy.deepcopy(env)};graph.env_reads=[]
    def selected(identity):
        graph.backend._require_live(identity,"fixture native changed")
        graph.env_reads.append(identity["pid"])
        return copy.deepcopy(graph.environments[identity["pid"]])
    graph.backend.selected_environment=selected
    graph.rows[40]={**graph.rows[30],"pid":40,"created":130.0,
        "sid":"S-1-5-21-fixture-1009","exe":"runner.exe","ppid":30}
    graph.rows[50]={**graph.rows[40],"pid":50,"created":140.0,"exe":"powershell.exe","ppid":40}
    graph.edges.update({30:[40],40:[50],50:[]})
    return graph,native,env

def discover(graph,native,env):
    return graph.backend.native_control_plane(graph.root,executable=native,
        expected_sha256=app.sha(native),environment=env)

def test_verified_control_plane_stops_only_at_bound_native_main_loop(monkeypatch,tmp_path):
    graph,native,env=configured(monkeypatch,tmp_path)
    assert discover(graph,native,env)==[graph.rows[10],graph.rows[20],graph.rows[30]]
    assert graph.children_calls==[(10,False),(20,False)]
    assert 40 not in graph.identity_calls and 50 not in graph.identity_calls
    assert graph.env_reads==[10,30,30,10]

def test_generic_tree_guard_still_rejects_other_account_workloads(monkeypatch,tmp_path):
    graph,_,_=configured(monkeypatch,tmp_path)
    with pytest.raises(app.LaunchError,match="unexpected owner/session"):
        graph.backend.descendants(graph.root)
    assert 40 in graph.identity_calls

@pytest.mark.parametrize("fault",["root_sid","root_elevated","root_home","backend_sid","backend_session","backend_elevated","foreign_prefix",
    "wrong_path","wrong_hash","wrong_home","wrong_frontend","wrong_cli_env","missing_env",
    "generator","help","version","native_pid_reuse","stale_parent_edge"])
def test_unverified_native_never_becomes_workload_boundary(monkeypatch,tmp_path,fault):
    graph,native,env=configured(monkeypatch,tmp_path);expected=app.sha(native)
    if fault=="root_sid":graph.root["sid"]="S-1-5-21-foreign"
    elif fault=="root_elevated":graph.rows[10]["elevated"]=True;graph.root["elevated"]=True
    elif fault=="root_home":graph.environments[10]["CODEX_HOME"]="foreign"
    elif fault=="backend_elevated":graph.rows[30]["elevated"]=True
    elif fault=="backend_sid":graph.rows[30]["sid"]="S-1-5-21-foreign"
    elif fault=="backend_session":graph.rows[30]["session"]=2
    elif fault=="foreign_prefix":graph.rows[20]["sid"]="S-1-5-21-foreign"
    elif fault=="wrong_path":
        other=tmp_path/"rogue/codex.exe";other.parent.mkdir();other.write_bytes(native.read_bytes())
        graph.rows[30]["exe"]=str(other)
    elif fault=="wrong_hash":expected="0"*64
    elif fault=="wrong_home":graph.environments[30]["CODEX_HOME"]="foreign"
    elif fault=="wrong_frontend":graph.environments[30]["CODEX_ELECTRON_USER_DATA_PATH"]="foreign"
    elif fault=="wrong_cli_env":graph.environments[30]["CODEX_CLI_PATH"]="foreign"
    elif fault=="missing_env":graph.environments[30].pop("CODEX_APP_SERVER_FORCE_CLI")
    elif fault in {"generator","help","version"}:
        graph.arguments[30]=[str(native),"app-server",
            {"generator":"generate-json-schema","help":"--help","version":"--version"}[fault]]
    elif fault=="native_pid_reuse":graph.hooks[("cmdline",30)]=lambda:graph.rows[30].update(created=999.0)
    else:graph.rows[30]["ppid"]=999
    with pytest.raises(app.LaunchError):
        graph.backend.native_control_plane(graph.root,executable=native,
            expected_sha256=expected,environment=env)

@pytest.mark.parametrize("fault",["image","environment","role","parent"])
def test_boundary_revalidated_after_enumeration(monkeypatch,tmp_path,fault):
    graph,native,env=configured(monkeypatch,tmp_path);expected=app.sha(native)
    if fault=="image":
        values=iter([expected,"0"*64]);monkeypatch.setattr(app,"sha",lambda path:next(values))
    elif fault=="environment":
        original=graph.backend.selected_environment
        def selected(identity):
            result=original(identity)
            if graph.env_reads.count(30)==1:graph.environments[30]["CODEX_HOME"]="changed-after-prune"
            return result
        graph.backend.selected_environment=selected
    else:
        original=graph.backend.native_role
        def role(identity):
            result=original(identity)
            if fault=="role":graph.arguments[30]=[str(native),"--version"]
            else:graph.rows[30]["ppid"]=999
            return result
        graph.backend.native_role=role
    with pytest.raises(app.LaunchError):
        graph.backend.native_control_plane(graph.root,executable=native,
            expected_sha256=expected,environment=env)

@pytest.mark.parametrize("at",["image","parent"])
def test_redirected_native_path_cannot_authorize_pruning(monkeypatch,tmp_path,at):
    graph,native,env=configured(monkeypatch,tmp_path);suspect=native if at=="image" else native.parent
    original=app._is_link_or_reparse
    monkeypatch.setattr(app,"_is_link_or_reparse",lambda path:Path(path)==suspect or original(path))
    with pytest.raises(app.LaunchError,match="redirect"):discover(graph,native,env)
    assert not graph.children_calls

def test_invalid_binding_rejected_before_process_walk(monkeypatch,tmp_path):
    graph,native,env=configured(monkeypatch,tmp_path)
    with pytest.raises(app.LaunchError):
        graph.backend.native_control_plane(graph.root,executable=native,
            expected_sha256="not-a-digest",environment=env)
    assert not graph.children_calls
