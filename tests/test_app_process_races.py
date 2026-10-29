"""Process admission races; fake graph only, no real App/native/model activity."""
import pytest
from codex_app_deepseek import isolated_app as app
from test_app_control_plane import configured,discover
from test_app_status_generations import generation
def sibling(graph):
 graph.rows[60]={**graph.rows[20],"pid":60,"created":115.0,"ppid":10,"exe":"utility.exe"}
 graph.edges[10]=[20,60];graph.edges[60]=[]
def test_unadmitted_non_native_exit_after_identity_does_not_abort_current_boundary(monkeypatch,tmp_path):
 graph,native,env=configured(monkeypatch,tmp_path);sibling(graph)
 graph.hooks[("identity",60)]=lambda:graph.rows.pop(60)
 found=discover(graph,native,env)
 assert [x["pid"]for x in found]==[10,20,30]
 assert graph.rows[30]["pid"]==30 and graph.rows[30]["created"]==120.0
 assert graph.env_reads==[10,30,30,10]
def test_lost_parent_during_identity_is_not_skipped_as_missing_child(monkeypatch,tmp_path):
 graph,native,env=configured(monkeypatch,tmp_path)
 graph.hooks[("identity",30)]=lambda:graph.rows.pop(20)
 with pytest.raises(app.psutil.NoSuchProcess)as error:discover(graph,native,env)
 assert error.value.pid==20
def test_identified_codex_actor_loss_is_never_optional(monkeypatch,tmp_path):
 graph,native,env=configured(monkeypatch,tmp_path)
 graph.hooks[("identity",30)]=lambda:graph.rows.pop(30)
 with pytest.raises(app.psutil.NoSuchProcess)as error:discover(graph,native,env)
 assert error.value.pid==30
def test_known_foreign_identity_cannot_escape_validation_by_disappearing(monkeypatch,tmp_path):
 graph,native,env=configured(monkeypatch,tmp_path);sibling(graph)
 graph.rows[60]["sid"]="S-1-5-21-foreign"
 graph.hooks[("identity",60)]=lambda:graph.rows.pop(60)
 with pytest.raises(app.LaunchError,match="unexpected owner/session"):discover(graph,native,env)
def test_access_denied_is_not_natural_exit(monkeypatch,tmp_path):
 graph,native,env=configured(monkeypatch,tmp_path);sibling(graph);original=graph.backend.identity
 def identity(pid):
  if pid==60:raise app.psutil.AccessDenied(pid)
  return original(pid)
 graph.backend.identity=identity
 with pytest.raises(app.psutil.AccessDenied):discover(graph,native,env)
def test_already_admitted_auxiliary_loss_stays_unverified(monkeypatch,tmp_path):
 graph,native,env=configured(monkeypatch,tmp_path);sibling(graph)
 graph.hooks[("children",60)]=lambda:graph.rows.pop(60)
 with pytest.raises(app.psutil.NoSuchProcess):discover(graph,native,env)
def test_status_keeps_missing_subject_pid_and_never_claims_ready(generation):
 run,_,_,_,backend=generation;original=(run/"receipt.json").read_bytes()
 def disappear(*args,**kwargs):raise app.psutil.NoSuchProcess(60)
 backend.native_control_plane=disappear
 result=app.launch_status(run,backend)["current_backend_observation"]
 assert result["status"]=="unknown"and result["error_type"]=="NoSuchProcess"and result["error_pid"]==60
 assert result["reason"]=="current_control_plane_unverified"and not result["native_processes"]
 assert (run/"receipt.json").read_bytes()==original
