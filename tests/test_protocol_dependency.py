"""Verify the installed fixed pure dependency and deny runtime import coupling."""
import hashlib
import importlib
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys
from codex_app_deepseek import gateway_policy

COMMIT='e83b4b3fa79714fa77b66ba4e96ae10abb9d6aad'
ARCHIVE='6f4fed84a3e4e65dd405d3a90e62b3d9ecb2a10d863f0ddbbfc82b51a35064a3'
PURE_HASHES={'__init__.py': 'f97e5df41a08ec4ce4bb059ca7b8dfbcd752878c6bdff502e2f2167f387f8cc7', 'formats.py': '3f8f027b56731c30ec0ffb518da9a09671059b364785a8e39688fb1052ea8f47', 'image_selection.py': 'b1993584f1893546d536e258285f5cfbffa94c94a643247f1b664db5c169cdd8', 'protocol.py': 'fd4a552e07c7d054e860926713e843d7f05e75364205a71a17385fcba9118482', 'protocol_errors.py': '446636771b39e39d36065a4ace549f4c5608d1f9005ff2723c75a573e1129452', 'request_metadata.py': 'bc693cd985e62b6e8591223aeb6ee069e77a2e4a585612082348cc2c36ae3cec', 'search.py': '438a157d92e0c1e717fbfe7c9f7856df1edf38cf3d9a0d63188636e693500030', 'structured.py': '8f54e9a5ad190bcfdd9d580adc00b184d5d440cd7376f5c5465a9a6c2e1c9d8a'}
TRANSPORT_HASHES={'image_transport.py': '153536d9c9809fef59d9510def266d77469d37300083c0dbcab8e9d8c998cb5a', 'platforms.py': '1bb26549997abfe14facff2e470bb7ce006da5a4a9083695062d2c4da5688c94', 'processes.py': '3ca59198e616ef7c7929b6a54645219e61c278da3b541e4d8872d6bdb95968f3', 'windows_asyncio.py': 'd2778f52c6978dd61c70e0d6c384f012d654bad72ff7119c2baf1e7ae67441a5'}
PSF_MEMBER='codex_deepseek_runtime-0.7.0.dist-info/licenses/licenses/cpython-proactor-PSF.txt'
PSF_SHA256='018bb8be8699b84bd2357128c7f1bd7d77af6ce5bbac143cb0cabb9a8ca3031d'


def test_source_lock_separates_pure_protocol_and_reviewed_transport_helpers():
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
    assert row["source"]["public_source_verified"] is True
    assert row["source"]["artifact_sha256"] == row["sha256"] == "1976aee41b47a8c9b322f4da1a7f7b355c64343cb6dd5eaa845d0273dee04470"
    assert row["source"]["source_date_epoch"] == 1794025472


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
    assert row["version"] == "0.7.0" and row["filename"] == "codex_deepseek_runtime-0.7.0-py3-none-any.whl"
    assert row["sha256"] == "1976aee41b47a8c9b322f4da1a7f7b355c64343cb6dd5eaa845d0273dee04470"
    assert row["source"]["type"] == "fixed_public_source_archive_local_wheel"
    assert row["source"]["public_source_verified"] is True
    assert row["source"]["source_commit"] == COMMIT
    assert row["source"]["archive_url"] == expected_archive and row["source"]["archive_sha256"] == ARCHIVE
    assert "artifact_url" not in row["source"]
    assert row["source"]["artifact_origin"] == "two_reproducible_local_wheels_from_exact_public_source"
    assert "release_tag" not in row["source"]
    assert row["source"]["source_date_epoch"] == 1794025472
    assert row["source"]["build_backend"] == "setuptools.build_meta"
    assert row["source"]["build_tools"] == {"setuptools": "84.2.0", "wheel": "0.48.0"}
    assert row["source"]["reproducibility"] == "two_independent_local_wheels_equal"
    assert row["source"]["artifact_sha256"] == row["sha256"]
    assert lock["protocol_boundary"] == {"source_commit": COMMIT, "pure_files": PURE_HASHES}
    assert lock["transport_compatibility"] == {"source_commit":COMMIT,"files":TRANSPORT_HASHES,"license_files":{PSF_MEMBER:PSF_SHA256}}
    assert metadata.version("codex-deepseek-runtime")=="0.7.0"
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
