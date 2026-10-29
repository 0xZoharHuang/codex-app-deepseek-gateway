"""Redirect rejection before existing storage/lifecycle side effects."""
from pathlib import Path
from types import SimpleNamespace
import ctypes
import json
import pytest
from codex_app_deepseek import app_config,cli,image_transport,image_maintenance,image_call_reconciliation,lifecycle
from test_web_storage_reparse import actual_dir_redirect,remove_new_junction_APIs

@pytest.fixture
def redirected(tmp_path,monkeypatch):
 target=tmp_path/"unrelated-owned-target";target.mkdir()
 canary=target/"user-canary.txt";canary.write_bytes(b"retain exact own fixture bytes")
 link=tmp_path/"redirect";actual_dir_redirect(link,target)
 remove_new_junction_APIs(monkeypatch)
 yield link,target,canary
 assert canary.read_bytes()==b"retain exact own fixture bytes"

def test_config_prepare_rejects_redirect_before_config_read(redirected,monkeypatch):
 link,target,canary=redirected
 reads=[]
 def forbidden(*args):reads.append(args);raise AssertionError("Config data must not be read before path admission")
 monkeypatch.setattr(app_config,"_read_optional",forbidden)
 with pytest.raises(app_config.ConfigurationError,match="symlink or junction"):
  app_config.prepare(link/"config.toml",catalog_path=target/"catalog.json",auth_command=target/"auth.exe",port=42817)
 assert not reads and sorted(p.name for p in target.iterdir())==["user-canary.txt"]

def test_config_apply_backup_redirect_rejected_before_acl_or_write(tmp_path,redirected,monkeypatch):
 link,target,_=redirected
 catalog=tmp_path/"catalog.json";catalog.write_text(json.dumps({"models":[{"slug":app_config.PINNED_MODEL}]}))
 auth=tmp_path/"auth.exe";auth.write_bytes(b"not executed")
 config=tmp_path/"config.toml";config.write_bytes(b"# untouched original\n")
 plan=app_config.prepare(config,catalog_path=catalog,auth_command=auth,port=42817)
 effects=[]
 monkeypatch.setattr(app_config,"_private",lambda *a,**kw:effects.append("private"))
 monkeypatch.setattr(app_config,"_atomic_write",lambda *a,**kw:effects.append("write"))
 with pytest.raises(app_config.ConfigurationError,match="symlink or junction"):
  app_config.apply(plan,backup_dir=link)
 assert not effects and config.read_bytes()==b"# untouched original\n"
 assert sorted(p.name for p in target.iterdir())==["user-canary.txt"]

def test_image_cache_constructor_rejects_redirect_before_native_acl_or_credentials(redirected,monkeypatch):
 link,target,_=redirected
 effects=[]
 monkeypatch.setattr(ctypes,"WinDLL",lambda *a,**kw:effects.append("native_acl")or pytest.fail("No Win32 ACL call before path admission"))
 def credential():effects.append("credential");raise AssertionError("No real credential")
 with pytest.raises(image_transport.ImageTransportError,match="link or junction"):
  image_transport.ImageTransport(link,credential,object(),image_transport.TransportPolicy(1))
 assert not effects and sorted(p.name for p in target.iterdir())==["user-canary.txt"]

async def test_image_lock_rejects_redirect_before_open_append(redirected):
 link,target,_=redirected
 with pytest.raises(image_transport.ImageTransportError,match="link"):
  async with image_transport._file_lock(link,1):
   pytest.fail("Redirected lock was opened")
 assert sorted(p.name for p in target.iterdir())==["user-canary.txt"]

def test_maintenance_rejects_redirect_before_journal_write(redirected,monkeypatch):
 link,target,_=redirected
 effects=[]
 monkeypatch.setattr(image_maintenance,"_atomic_write",lambda *a,**kw:effects.append("write"))
 with pytest.raises(image_maintenance.ImageMaintenanceError,match="private regular"):
  image_maintenance.ImageMaintenance(link/"maintenance.json",lambda:pytest.fail("No backend"))
 assert not effects and sorted(p.name for p in target.iterdir())==["user-canary.txt"]

async def test_reconciliation_rejects_redirect_before_credentials_lock_or_network(tmp_path,redirected,monkeypatch):
 link,target,_=redirected
 receipt=tmp_path/"receipts.sqlite";receipt.write_bytes(b"not read")
 effects=[]
 monkeypatch.setattr(image_call_reconciliation,"_atomic_write",lambda *a,**kw:effects.append("write"))
 def credentials():effects.append("credentials");raise AssertionError("No credential access")
 with pytest.raises(image_call_reconciliation.CallReconciliationError,match="private regular"):
  await image_call_reconciliation.reconcile_image_calls(object(),receipt,link/"reconcile.json",credentials)
 assert not effects and sorted(p.name for p in target.iterdir())==["user-canary.txt"]

def test_runtime_provenance_rejects_redirect_before_metadata_digest(redirected,monkeypatch):
 link,target,_=redirected
 image=target/"python.exe";image.write_bytes(b"not executed")
 effects=[]
 monkeypatch.setattr(lifecycle,"digest",lambda *a:effects.append("digest")or pytest.fail("No image read"))
 with pytest.raises(lifecycle.LifecycleError,match="link or junction"):
  lifecycle._runtime_regular_file(link/"python.exe")
 assert not effects and image.read_bytes()==b"not executed"

def test_lifecycle_inventory_rejects_redirect_before_traversal_reads(tmp_path,monkeypatch):
 managed=tmp_path/"managed";managed.mkdir();outside=tmp_path/"outside";outside.mkdir()
 canary=outside/"outside.txt";canary.write_bytes(b"outside")
 actual_dir_redirect(managed/"redirect",outside);remove_new_junction_APIs(monkeypatch)
 effects=[]
 monkeypatch.setattr(lifecycle,"digest",lambda *a:effects.append("digest")or pytest.fail("No redirected inventory read"))
 with pytest.raises(lifecycle.LifecycleError,match="links or junctions"):
  lifecycle._inventory(managed)
 assert not effects and canary.read_bytes()==b"outside"

def test_cli_image_factory_rejects_redirect_before_backend_creation(tmp_path,monkeypatch):
 home=tmp_path/"home";home.mkdir();outside=tmp_path/"outside";outside.mkdir()
 actual_dir_redirect(home/"image-transport",outside);remove_new_junction_APIs(monkeypatch)
 config=cli.ServiceConfig((app_config.PINNED_MODEL,),("deepseek",),image_transport={"activate_above_bytes":1})
 effects=[]
 monkeypatch.setattr(cli,"read",lambda *a:effects.append("credential")or pytest.fail("No credential access"))
 with pytest.raises(cli.ServiceConfigError,match="link or junction"):
  cli.image_transport_factory(home,config)
 assert not effects and list(outside.iterdir())==[]
