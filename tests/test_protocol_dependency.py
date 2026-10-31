"""Verify the installed fixed pure dependency and deny runtime import coupling."""
import hashlib
import importlib
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys
from codex_app_deepseek import gateway_policy

COMMIT="1684b7f136b59b04ca7a1895d0754f30f88f5a45"
ARCHIVE="9551d221a96b580253057bf752f4b68b32de9e883a5d6becb3e7bb76cdb45738"
PURE_HASHES={'protocol.py': 'f878a1091a48ecd195c5616d481f68c846f67ba533d6b12b1cb68a452850094a', 'formats.py': '3f8f027b56731c30ec0ffb518da9a09671059b364785a8e39688fb1052ea8f47', 'structured.py': '8f54e9a5ad190bcfdd9d580adc00b184d5d440cd7376f5c5465a9a6c2e1c9d8a', 'protocol_errors.py': '446636771b39e39d36065a4ace549f4c5608d1f9005ff2723c75a573e1129452', 'search.py': '438a157d92e0c1e717fbfe7c9f7856df1edf38cf3d9a0d63188636e693500030', '__init__.py': '50781f55b6e19ac086738732a2cc50fc6cd62a4fa8823fe0a40f4f085641e6c4'}
TRANSPORT_HASHES={"windows_asyncio.py":"d2778f52c6978dd61c70e0d6c384f012d654bad72ff7119c2baf1e7ae67441a5"}
PSF_MEMBER="codex_deepseek_runtime-0.6.8.dist-info/licenses/licenses/cpython-proactor-PSF.txt"
PSF_SHA256="018bb8be8699b84bd2357128c7f1bd7d77af6ce5bbac143cb0cabb9a8ca3031d"


def test_source_lock_keeps_pure_protocol_separate_from_single_transport_helper():
    import tomllib
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    required = [value for value in project["project"]["dependencies"]
                if value.startswith("codex-deepseek-runtime")]
    expected = ("codex-deepseek-runtime @ https://codeload.github.com/0xZoharHuang/"
                "codex-deepseek-runtime/tar.gz/" + COMMIT + "#sha256=" + ARCHIVE)
    assert required == [expected]
    lock = json.loads((root / "release/dependencies.windows-py313.json").read_bytes())
    assert lock["protocol_boundary"] == {"source_commit": COMMIT, "pure_files": PURE_HASHES}
    assert lock["transport_compatibility"] == {
        "source_commit": COMMIT, "files": TRANSPORT_HASHES, "license_files": {PSF_MEMBER: PSF_SHA256}}
    assert set(PURE_HASHES).isdisjoint(TRANSPORT_HASHES)
    row, = [value for value in lock["dependencies"] if value["distribution"] == "codex-deepseek-runtime"]
    assert row["source"]["source_commit"] == COMMIT and row["source"]["archive_sha256"] == ARCHIVE
    assert row["source"]["archive_url"].endswith("/" + COMMIT)
    assert row["source"]["public_release_ready"] is True
    assert row["source"]["artifact_sha256"] == row["sha256"] == "a4f52d6d82745c8b0acf17d6e7f595184e5cea1f55d7ac4443d4626dc0a5bd30"
    assert row["source"]["source_date_epoch"] == 1793467477


def test_gateway_wheel_pins_fixed_protocol_archive_and_actual_installed_version():
    requires=metadata.requires("codex-app-deepseek-gateway")
    target=[x for x in requires if x.startswith("codex-deepseek-runtime")]
    assert len(target)==1 and COMMIT in target[0] and "#sha256="+ARCHIVE in target[0]
    expected_archive = "https://codeload.github.com/0xZoharHuang/codex-deepseek-runtime/tar.gz/" + COMMIT
    assert target[0].split("@", 1)[1].strip() == expected_archive + "#sha256=" + ARCHIVE
    lock = json.loads((Path(__file__).resolve().parents[1] / "release/dependencies.windows-py313.json").read_text())
    rows = [row for row in lock["dependencies"] if row["distribution"] == "codex-deepseek-runtime"]
    assert len(rows) == 1
    row = rows[0]
    assert row["version"] == "0.6.8" and row["filename"] == "codex_deepseek_runtime-0.6.8-py3-none-any.whl"
    assert row["sha256"] == "a4f52d6d82745c8b0acf17d6e7f595184e5cea1f55d7ac4443d4626dc0a5bd30"
    assert row["source"]["type"] == "fixed_public_source_archive_local_wheel"
    assert row["source"]["public_release_ready"] is True
    assert row["source"]["source_commit"] == COMMIT
    assert row["source"]["archive_url"] == expected_archive and row["source"]["archive_sha256"] == ARCHIVE
    assert row["source"]["artifact_url"] == "https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/download/v0.6.8/" + row["filename"]
    assert row["source"]["release_tag"] == "v0.6.8"
    assert row["source"]["source_date_epoch"] == 1793467477
    assert row["source"]["build_backend"] == "setuptools.build_meta"
    assert row["source"]["build_tools"] == {"setuptools": "84.2.0", "wheel": "0.48.0"}
    assert row["source"]["reproducibility"] == "two_independent_local_wheels_equal"
    assert row["source"]["artifact_sha256"] == row["sha256"]
    assert lock["protocol_boundary"] == {"source_commit": COMMIT, "pure_files": PURE_HASHES}
    assert lock["transport_compatibility"] == {"source_commit":COMMIT,"files":TRANSPORT_HASHES,"license_files":{PSF_MEMBER:PSF_SHA256}}
    assert metadata.version("codex-deepseek-runtime")=="0.6.8"
    assert gateway_policy.DEFAULT_POLICY_ORIGIN["protocol_commit"]==COMMIT
    for name,digest in {**PURE_HASHES,**TRANSPORT_HASHES}.items():
        module=importlib.import_module("codex_deepseek"+("."+name[:-3] if name!="__init__.py" else ""))
        path=Path(module.__file__).resolve()
        assert path.is_relative_to(Path(sys.prefix).resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest()==digest

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
