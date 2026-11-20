"""Verify the installed fixed pure dependency and deny runtime import coupling."""
import hashlib
import importlib
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys
from codex_app_deepseek import gateway_policy

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "release/dependencies.windows-py313.json").read_bytes())
COMMIT = "66f55c22b2d4b4041099509a014e12e3be780260"
CDS_SHA = "28ca55004bde2817388c6552074c3b9e154025087629a1f518632d32789a727f"
AIOHTTP_SHA = "f17624f46dd60b35aaf21d172bf10521d161fdf8322541b91c09c5d36bf2b73a"
PURE_HASHES = LOCK["protocol_boundary"]["pure_files"]
TRANSPORT_HASHES = LOCK["transport_compatibility"]["files"]
PSF_MEMBER = "codex_deepseek_runtime-0.7.9+cadsg.2.dist-info/licenses/licenses/cpython-proactor-PSF.txt"
PSF_SHA = "018bb8be8699b84bd2357128c7f1bd7d77af6ce5bbac143cb0cabb9a8ca3031d"


def test_source_lock_separates_pure_protocol_and_reviewed_transport_helpers():
    assert set(PURE_HASHES) == {
        "__init__.py", "error_privacy.py", "formats.py", "image_selection.py", "native_context.py", "opaque_replay.py",
        "protocol.py", "protocol_errors.py", "request_metadata.py", "search.py", "structured.py",
    }
    assert set(TRANSPORT_HASHES) == {"image_transport.py", "platforms.py", "processes.py", "windows_asyncio.py"}
    assert LOCK["transport_compatibility"]["license_files"] == {PSF_MEMBER: PSF_SHA}
    assert LOCK["protocol_boundary"]["source_commit"] == LOCK["transport_compatibility"]["source_commit"] == COMMIT
    assert gateway_policy.DEFAULT_POLICY_ORIGIN["protocol_commit"] == COMMIT

    rows = {row["distribution"]: row for row in LOCK["dependencies"]}
    cds, aiohttp = rows["codex-deepseek-runtime"], rows["aiohttp"]
    assert cds["sha256"] == cds["source"]["artifact_sha256"] == CDS_SHA
    assert cds["source"]["source_commit"] == COMMIT and cds["source"]["public_source_verified"] is False
    assert aiohttp["sha256"] == aiohttp["source"]["artifact_sha256"] == AIOHTTP_SHA
    assert aiohttp["source"]["official_upstream_wheel"] is False


def test_gateway_wheel_metadata_and_installed_protocol_match_offline_lock():
    import tomllib
    from packaging.requirements import Requirement

    expected = {"codex-deepseek-runtime": "0.7.9+cadsg.2", "aiohttp": "3.14.5+cds.1"}
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for requirements in (project["project"]["dependencies"], metadata.requires("codex-app-deepseek-gateway")):
        pins = {req.name: str(req.specifier) for req in map(Requirement, requirements) if req.name in expected}
        assert pins == {name: "==" + version for name, version in expected.items()}
    for name, version in expected.items():
        assert metadata.version(name) == version
    for name, digest in {**PURE_HASHES, **TRANSPORT_HASHES}.items():
        module = importlib.import_module("codex_deepseek" + ("." + name[:-3] if name != "__init__.py" else ""))
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(Path(sys.prefix).resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_fresh_gateway_import_never_uses_cds_runtime_execution_modules(tmp_path):
    program=r"""
import importlib.abc,sys,json
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.startswith("codex_deepseek.") and fullname not in ALLOWED:
   raise RuntimeError("Forbidden CDS runtime coupling: "+fullname)
sys.meta_path.insert(0,Boundary())
original = None
if sys.platform == "win32":
 from asyncio import proactor_events
 original = proactor_events._ProactorBasePipeTransport._call_connection_lost
from codex_app_deepseek import bridge,cli,firecrawl_backend,weather_backend,protocol_state,web_backend,upstream_evidence
from codex_app_deepseek.image_transport import plan_image_budget, TransportPolicy
assert plan_image_budget({"input":[]}, TransportPolicy(1)).diagnostics["upstream_inference_sent"] is False
if original is not None:
 assert proactor_events._ProactorBasePipeTransport._call_connection_lost is original
loaded = {name for name in sys.modules if name == "codex_deepseek" or name.startswith("codex_deepseek.")}
assert loaded <= ALLOWED
for control in ("codex_deepseek.browser", "codex_deepseek.image_provider", "codex_deepseek.native_http", "codex_deepseek.service", "codex_deepseek.supervisor"):
 try:
  __import__(control)
 except RuntimeError as error:
  assert str(error) == "Forbidden CDS runtime coupling: " + control
 else:
  raise AssertionError("Runtime boundary guard did not reject control import")
assert {name for name in sys.modules if name == "codex_deepseek" or name.startswith("codex_deepseek.")} <= ALLOWED
print(json.dumps({"pure_boundary":True}))
"""
    allowed={"codex_deepseek"+("."+name[:-3] if name!="__init__.py" else "") for name in {**PURE_HASHES,**TRANSPORT_HASHES}}
    program="ALLOWED="+repr(allowed)+"\n"+program
    result=subprocess.run([sys.executable,"-I","-B","-c",program],cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={"pure_boundary":True}
