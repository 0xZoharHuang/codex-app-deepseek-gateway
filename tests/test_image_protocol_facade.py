"""Prove App protocol reuse, cache dispatch and HTTP ownership boundaries."""
import asyncio
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from codex_deepseek import image_transport as core
from codex_app_deepseek import image_transport as app_images

def test_cache_mechanisms_are_the_actual_pinned_shared_methods():
    assert issubclass(app_images.ImageTransport,core.ImageTransport)
    for name in ("_scope","_db","_asset_lock","_acquire","prepare","cleanup",
                 "reconcile_unknown_uploads","settle_unknown","migrate_cache_format"):
        assert inspect.getattr_static(app_images.ImageTransport,name) is inspect.getattr_static(core.ImageTransport,name)
    assert app_images._CallLease is core._CallLease
    assert app_images._ledger_transaction is core._ledger_transaction
    assert app_images.PayloadBudgetExceeded is core.PayloadBudgetExceeded
    assert app_images.OpenRouterFilesBackend is not core.OpenRouterFilesBackend

def test_failure_injection_reaches_actual_shared_constructor(tmp_path,monkeypatch):
    calls=[]
    def unreadable(pid):
        calls.append(pid);return None
    monkeypatch.setattr(core,"process_identity",unreadable)
    root=tmp_path/"uncreated"
    with pytest.raises(app_images.ImageTransportError,match="Cannot confirm"):
        app_images.ImageTransport(root,lambda:pytest.fail("no key"),None,app_images.TransportPolicy(1))
    assert calls==[os.getpid()] and not root.exists()

def test_legacy_epoch_is_conservative_under_the_shared_owner_probe():
    import psutil
    stamp=psutil.Process().create_time()
    assert app_images._owner_alive(os.getpid(),stamp) is None
    assert app_images._owner_alive(os.getpid(),stamp-1) is None

def test_gateway_factory_keeps_its_actual_http_session_proxy_and_cache(tmp_path):
    from codex_app_deepseek.cli import image_transport_factory
    from codex_app_deepseek.gateway_policy import ServiceConfig
    client=object();credential=lambda:pytest.fail("construction must not read a key")
    config=ServiceConfig(("deepseek/deepseek-v4.1-flash-1017",),("deepseek",),
                         upstream_proxy="http://127.0.0.1:19876",image_transport={"activate_above_bytes":1})
    transport=image_transport_factory(tmp_path,config)(client,credential)
    assert type(transport) is app_images.ImageTransport
    assert type(transport.backend) is app_images.OpenRouterFilesBackend
    assert transport.backend.client is client
    assert transport.backend.proxy_url==config.upstream_proxy
    assert transport.credentials is credential
    assert transport.root==tmp_path/"image-transport"

def test_real_import_and_offline_cache_use_no_cds_runtime_provider(tmp_path):
    own_src=str(Path(app_images.__file__).resolve().parents[1])
    core_src=str(Path(core.__file__).resolve().parents[1])
    deps=[p for p in sys.path if "site-packages"in p]
    program=r"""import sys,builtins,json,asyncio,subprocess
from pathlib import Path
sys.path[:0]=json.loads(sys.argv[1])
forbidden={"codex_deepseek.cli","codex_deepseek.image_provider","codex_deepseek.provider","codex_deepseek.store","codex_deepseek.supervisor","codex_deepseek.native_http","codex_deepseek.windows_launchers","codex_deepseek.browser","codex_deepseek.web_mcp"}
original=builtins.__import__
def guarded(name,globals=None,locals=None,fromlist=(),level=0):
 full={name}|{name+"."+n for n in (fromlist or ()) if isinstance(n,str)}
 assert not full&forbidden,"Forbidden runtime import: "+str(full&forbidden)
 return original(name,globals,locals,fromlist,level)
builtins.__import__=guarded
def no(*a,**k):raise AssertionError("No native or external process")
subprocess.Popen=no;subprocess.run=no
from codex_app_deepseek.image_transport import ImageTransport,TransportPolicy
from codex_deepseek import processes
for name in ('terminate','kill_tree','terminate_tree'):
 if hasattr(processes,name):setattr(processes,name,no)
def key():raise AssertionError("Offline cache migration must not read credentials")
t=ImageTransport(Path(sys.argv[2]),key,None,TransportPolicy(1))
r=asyncio.run(t.migrate_cache_format(3))
assert r["complete"]is True and r["credentials_read"]is False and r["external_HTTP_requests"]==r["inference_requests"]==0
assert not forbidden&set(sys.modules)
print(json.dumps({"complete":True,"forbidden_imports":[],"native_processes":0,"keys":0}))
"""
    p=subprocess.run([sys.executable,"-I","-B","-Werror","-c",program,json.dumps([own_src,core_src,*deps]),str(tmp_path/"isolated-cache")],capture_output=True,text=True,timeout=30)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)=={"complete":True,"forbidden_imports":[],"native_processes":0,"keys":0}
