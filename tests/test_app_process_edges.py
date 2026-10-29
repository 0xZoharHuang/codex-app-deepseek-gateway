"""Pure process-graph counterexamples; no App, subprocess, or model is launched."""
from pathlib import Path
import copy,importlib.util,sys
import pytest
from codex_app_deepseek import isolated_app as app
class Graph:
    def __init__(self,monkeypatch):
        self.rows={
            10:{"pid":10,"created":100.0,"sid":"S-1-5-21-fixture-1001","session":1,"exe":"ChatGPT.exe","ppid":1,"elevated":False},
            20:{"pid":20,"created":110.0,"sid":"S-1-5-21-fixture-1001","session":1,"exe":"electron.exe","ppid":10,"elevated":False},
            30:{"pid":30,"created":120.0,"sid":"S-1-5-21-fixture-1001","session":1,"exe":"codex.exe","ppid":20,"elevated":False},
        }
        self.arguments={30:["codex.exe","app-server"]};self.edges={10:[20],20:[30],30:[]};self.hooks={};self.identity_calls=[];self.children_calls=[];self.cmdline_calls=[]
        graph=self
        class Process:
            def __init__(self,pid):
                if pid not in graph.rows:raise app.psutil.NoSuchProcess(pid)
                self.pid=pid;self.cached_created=None
            def create_time(self):
                if self.cached_created is None:self.cached_created=graph.rows[self.pid]["created"]
                return self.cached_created
            def ppid(self):return graph.rows[self.pid]["ppid"]
            def children(self,recursive=False):
                graph.children_calls.append((self.pid,recursive))
                children=[Process(pid) for pid in graph.edges.get(self.pid,[])]
                for child in children:child.create_time()
                hook=graph.hooks.pop(("children",self.pid),None)
                if hook:hook()
                return children
            def cmdline(self):
                graph.cmdline_calls.append(self.pid)
                hook=graph.hooks.pop(("cmdline",self.pid),None)
                if hook:hook()
                return list(graph.arguments.get(self.pid,[graph.rows[self.pid]["exe"]]))
        monkeypatch.setattr(app.psutil,"Process",Process)
        self.backend=object.__new__(app.WindowsBackend)
        def identity(pid):
            self.identity_calls.append(pid)
            if pid not in self.rows:raise app.psutil.NoSuchProcess(pid)
            result=copy.deepcopy(self.rows[pid])
            hook=self.hooks.pop(("identity",pid),None)
            if hook:hook()
            return result
        self.backend.identity=identity
        self.root=copy.deepcopy(self.rows[10])
def test_actual_descendants_keep_normal_app_child_and_grandchild(monkeypatch):
    graph=Graph(monkeypatch)
    assert graph.backend.descendants(graph.root)==[graph.rows[10],graph.rows[20],graph.rows[30]]
    assert graph.children_calls==[(10,False),(20,False),(30,False)]
@pytest.mark.parametrize("fault",["static_stale_ppid","child_predates_current_parent","parent_reused_during_enumeration","parent_reused_during_child_read","child_reused_during_identity","child_ppid_changed_during_identity","foreign_owner","foreign_session","owner_expired","duplicate_cycle","parent_expires_before_final_ready"])
def test_uncertain_or_foreign_edges_never_produce_ready_tree(monkeypatch,fault):
    graph=Graph(monkeypatch)
    if fault=="static_stale_ppid":graph.rows[20].update(created=300.0,ppid=999)
    elif fault=="child_predates_current_parent":graph.rows[20]["created"]=130.0
    elif fault=="parent_reused_during_enumeration":graph.hooks[("children",20)]=lambda:graph.rows[20].update(created=300.0)
    elif fault=="parent_reused_during_child_read":graph.hooks[("identity",30)]=lambda:graph.rows[20].update(created=300.0)
    elif fault=="child_reused_during_identity":graph.hooks[("identity",30)]=lambda:graph.rows[30].update(created=300.0)
    elif fault=="child_ppid_changed_during_identity":
        def change():graph.rows[30]["ppid"]=999
        # Return an already changed current identity versus the basic edge read.
        original=graph.backend.identity
        def identity(pid):
            if pid==30:change()
            return original(pid)
        graph.backend.identity=identity
    elif fault=="foreign_owner":graph.rows[30]["sid"]="S-1-5-21-other-1001"
    elif fault=="foreign_session":graph.rows[30]["session"]=2
    elif fault=="owner_expired":graph.rows[10]["created"]=400.0
    elif fault=="duplicate_cycle":graph.edges[30]=[10];graph.rows[10]["ppid"]=30
    elif fault=="parent_expires_before_final_ready":graph.hooks[("children",30)]=lambda:graph.rows[20].update(created=300.0)
    with pytest.raises(app.LaunchError):graph.backend.descendants(graph.root)
    if fault=="static_stale_ppid":assert 20 not in graph.identity_calls
    if fault=="owner_expired":assert not graph.children_calls
def test_vanished_unadmitted_child_is_not_adopted_or_inspected(monkeypatch):
    graph=Graph(monkeypatch)
    graph.hooks[("children",20)]=lambda:graph.rows.pop(30)
    assert graph.backend.descendants(graph.root)==[graph.rows[10],graph.rows[20]]
    assert 30 not in graph.identity_calls
def test_native_role_uses_fresh_birth_after_cached_object_command_read(monkeypatch):
    graph=Graph(monkeypatch);item=copy.deepcopy(graph.rows[30])
    graph.hooks[("cmdline",30)]=lambda:graph.rows[30].update(created=500.0)
    with pytest.raises(app.LaunchError,match="native identity changed"):graph.backend.native_role(item)

def test_identity_rechecks_birth_with_new_process_after_cached_metadata(monkeypatch):
    import ctypes
    graph=Graph(monkeypatch);backend=graph.backend
    # Read the real identity implementation with mock Win32 token calls.
    keep=ctypes.create_unicode_buffer(graph.rows[30]["sid"])
    class Kernel:
        def OpenProcess(self,*args):return 1
        def CloseHandle(self,*args):pass
        def LocalFree(self,*args):pass
        def ProcessIdToSessionId(self,pid,ptr):ptr._obj.value=1;return 1
    class Advapi:
        def OpenProcessToken(self,handle,access,ptr):ptr._obj.value=2;return 1
        def GetTokenInformation(self,token,kind,buffer,size,needed):
            needed._obj.value=32
            if kind==20:buffer._obj.value=0
            return 1
        def ConvertSidToStringSidW(self,sid,ptr):ptr._obj.value=ctypes.addressof(keep);return 1
    backend.kernel=Kernel();backend.advapi=Advapi()
    original_process=app.psutil.Process
    class Process:
        def __init__(self,pid):self.inner=original_process(pid)
        def create_time(self):return self.inner.create_time()
        def ppid(self):return graph.rows[30]["ppid"]
        def exe(self):
            graph.rows[30]["created"]=600.0
            return graph.rows[30]["exe"]
    monkeypatch.setattr(app.psutil,"Process",Process)
    with pytest.raises(app.LaunchError,match="identity changed during inspection"):
        app.WindowsBackend.identity(backend,30)

@pytest.mark.parametrize("stage",["enumerating_parent","final_parent"])
def test_normal_short_lived_intermediate_disappearance_is_retryable(monkeypatch,stage):
    graph=Graph(monkeypatch)
    if stage=="enumerating_parent":graph.hooks[("children",20)]=lambda:graph.rows.pop(20)
    else:graph.hooks[("children",30)]=lambda:graph.rows.pop(20)
    with pytest.raises(app.psutil.NoSuchProcess):graph.backend.descendants(graph.root)
def test_child_reused_after_enumeration_is_not_adopted(monkeypatch):
    graph=Graph(monkeypatch)
    graph.hooks[("children",20)]=lambda:graph.rows[30].update(created=800.0)
    with pytest.raises(app.LaunchError,match="PID changed after enumeration"):graph.backend.descendants(graph.root)
