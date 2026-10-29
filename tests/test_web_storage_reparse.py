"""Python-3.11-compatible web storage redirect guards; no native/App calls."""
from pathlib import Path
from types import SimpleNamespace
import os
import stat
import subprocess
import pytest
from codex_app_deepseek import web_backend as wb
from test_web_media_display import retained_png

def remove_new_junction_APIs(monkeypatch):
 for cls in Path.__mro__:
  if "is_junction" in cls.__dict__:
   monkeypatch.delattr(cls,"is_junction")
 monkeypatch.delattr(os.path,"isjunction",raising=False)
 assert not hasattr(Path,"is_junction") and not hasattr(os.path,"isjunction")

@pytest.mark.parametrize("mode,attributes,expected",[
 (stat.S_IFDIR|0o700,0x400,True),
 (stat.S_IFLNK|0o777,0,True),
 (stat.S_IFREG|0o600,0,False),
])
def test_lstat_guard_needs_no_new_junction_APIs(monkeypatch,mode,attributes,expected):
 remove_new_junction_APIs(monkeypatch)
 monkeypatch.setattr(wb.os,"lstat",lambda path:SimpleNamespace(st_mode=mode,st_file_attributes=attributes))
 assert wb._is_link_or_reparse(Path("owned-fixture")) is expected

def test_missing_path_is_allowed_but_unreadable_identity_does_not_fail_open(monkeypatch):
 remove_new_junction_APIs(monkeypatch)
 def missing(path):raise FileNotFoundError()
 monkeypatch.setattr(wb.os,"lstat",missing)
 assert wb._is_link_or_reparse(Path("missing")) is False
 def denied(path):raise PermissionError()
 monkeypatch.setattr(wb.os,"lstat",denied)
 with pytest.raises(PermissionError):wb._is_link_or_reparse(Path("unreadable"))

def actual_dir_redirect(link,target):
 if os.name=="nt":
  q=subprocess.run([r"C:\Windows\System32\cmd.exe","/d","/c","mklink","/J",str(link),str(target)],capture_output=True,text=True)
  assert q.returncode==0,q.stderr
  assert not link.is_symlink()  # A real junction is exactly the old fallback gap.
  assert os.lstat(link).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
 else:link.symlink_to(target,target_is_directory=True)

def test_real_store_ancestor_junction_is_rejected_without_new_APIs(tmp_path,monkeypatch):
 target=tmp_path/"elsewhere";target.mkdir();link=tmp_path/"redirect"
 actual_dir_redirect(link,target)
 remove_new_junction_APIs(monkeypatch)
 with pytest.raises(ValueError,match="symlink or junction"):
  wb.WebSourceStore(link/"sources.sqlite")
 assert list(target.iterdir())==[]

def test_real_generated_junction_is_rejected_without_new_APIs(tmp_path,monkeypatch):
 store=wb.WebSourceStore(tmp_path/"sources.sqlite")
 try:
  source,_=retained_png(store)
  target=tmp_path/"elsewhere";target.mkdir();link=tmp_path/"generated-media"
  actual_dir_redirect(link,target)
  remove_new_junction_APIs(monkeypatch)
  with pytest.raises(wb.WebBackendError)as error:
   store.generated_png_path("native-session-A",source["ref_id"])
  assert error.value.code=="generated_media_path_unsafe"
  assert list(target.iterdir())==[]
 finally:store.close()


def test_cli_serve_rejects_junction_before_credentials_or_private_side_effects(tmp_path,monkeypatch):
 from codex_app_deepseek import cli,app_config
 home=tmp_path/"owned-gateway-home";home.mkdir()
 target=tmp_path/"elsewhere";target.mkdir();actual_dir_redirect(home/"web-data",target)
 remove_new_junction_APIs(monkeypatch)
 private=[];credentials=[]
 monkeypatch.setattr(app_config,"_private",lambda *args,**kwargs:private.append((args,kwargs)))
 def read(*args):credentials.append(args);raise AssertionError("No credential lookup before path admission")
 monkeypatch.setattr(cli,"read",read)
 with pytest.raises(cli.ServiceConfigError,match="link or junction"):
  cli.serve(home,42817)
 assert not private and not credentials and list(target.iterdir())==[]
