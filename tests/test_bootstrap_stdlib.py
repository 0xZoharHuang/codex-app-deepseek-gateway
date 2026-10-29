"""The install/recovery process must work before runtime dependencies exist."""
import json
from pathlib import Path
import subprocess
import sys
from codex_app_deepseek import lifecycle

def test_real_package_bootstrap_writer_has_no_thirdparty_dependency(tmp_path):
    package_parent = Path(lifecycle.__file__).resolve().parent.parent
    script = r"""
from pathlib import Path
import sys,json,importlib,importlib.abc
package_parent=Path(sys.argv[1]);work=Path(sys.argv[2])
allowed=set(sys.stdlib_module_names)|set(sys.builtin_module_names)|{"codex_app_deepseek"}
blocked=[]
class RejectThirdParty(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split(".")[0]not in allowed:
   blocked.append(fullname)
   raise ModuleNotFoundError("bootstrap thirdparty forbidden: "+fullname,name=fullname)
sys.meta_path.insert(0,RejectThirdParty());sys.path.insert(0,str(package_parent))
for name in ["tomlkit","codex_deepseek_runtime","aiohttp","psutil","mcp"]:
 try:importlib.import_module(name)
 except ModuleNotFoundError:pass
 else:raise AssertionError("isolation guard inactive")
assert blocked==["tomlkit","codex_deepseek_runtime","aiohttp","psutil","mcp"]
blocked.clear()
from codex_app_deepseek import lifecycle as lc
target=work/"metadata"/"installation.json"
lc._write(target,{"stage":"installed","data_home":"fixture only"})
assert lc._json(target)=={"stage":"installed","data_home":"fixture only"}
lc._write(target,{"stage":"removed","data_home":"fixture only"})
assert lc._json(target)["stage"]=="removed"
for name,module in tuple(sys.modules.items()):
 if name.startswith("codex_app_deepseek.") and getattr(module,"__file__",None):
  assert Path(module.__file__).resolve().is_relative_to(package_parent/"codex_app_deepseek")
assert blocked==[],blocked
print(json.dumps({"isolated":bool(sys.flags.isolated),"no_site":bool(sys.flags.no_site),"unexpected_thirdparty":blocked,"real_package":str(Path(lc.__file__).resolve())}))
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-c", script,
                             str(package_parent), str(tmp_path)],
                            capture_output=True, text=True, encoding="utf-8",
                            timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["isolated"] and report["no_site"]
    assert report["unexpected_thirdparty"] == []
    assert Path(report["real_package"]) == Path(lifecycle.__file__).resolve()
