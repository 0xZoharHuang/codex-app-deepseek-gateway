"""Verify the installed fixed pure dependency and deny runtime import coupling."""
import hashlib
import importlib
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys
from codex_app_deepseek import gateway_policy

COMMIT="4d99981baaa493b0c4e7b1febbdb167e6c44de91"
ARCHIVE="34d9261c03e1c31156506ec963cc8c07227e08d50486b25efbea1acbcbb59c03"
PURE_HASHES={'protocol.py': 'f878a1091a48ecd195c5616d481f68c846f67ba533d6b12b1cb68a452850094a', 'formats.py': '3f8f027b56731c30ec0ffb518da9a09671059b364785a8e39688fb1052ea8f47', 'structured.py': '8f54e9a5ad190bcfdd9d580adc00b184d5d440cd7376f5c5465a9a6c2e1c9d8a', 'protocol_errors.py': '446636771b39e39d36065a4ace549f4c5608d1f9005ff2723c75a573e1129452', 'search.py': '438a157d92e0c1e717fbfe7c9f7856df1edf38cf3d9a0d63188636e693500030', '__init__.py': '50781f55b6e19ac086738732a2cc50fc6cd62a4fa8823fe0a40f4f085641e6c4'}

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
    assert row["sha256"] == "fd26450f13ed03cb549438e097802b4ed4b5a0476e463b9ab94c5082eabe42bd"
    assert row["source"]["type"] == "fixed_public_source_archive_local_wheel"
    assert row["source"]["public_release_ready"] is False
    assert row["source"]["source_commit"] == COMMIT
    assert row["source"]["archive_url"] == expected_archive and row["source"]["archive_sha256"] == ARCHIVE
    assert "artifact_url" not in row["source"] and "release_tag" not in row["source"]
    assert row["source"]["source_date_epoch"] == 1793440910
    assert row["source"]["build_backend"] == "setuptools.build_meta"
    assert row["source"]["build_tools"] == {"setuptools": "84.2.0", "wheel": "0.48.0"}
    assert row["source"]["reproducibility"] == "two_independent_local_wheels_equal"
    assert row["source"]["artifact_sha256"] == row["sha256"]
    assert lock["protocol_boundary"] == {"source_commit": COMMIT, "pure_files": PURE_HASHES}
    assert metadata.version("codex-deepseek-runtime")=="0.6.8"
    assert gateway_policy.DEFAULT_POLICY_ORIGIN["protocol_commit"]==COMMIT
    for name,digest in PURE_HASHES.items():
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
from codex_app_deepseek import bridge,protocol_state,web_backend,upstream_evidence
loaded = {name for name in sys.modules if name == "codex_deepseek" or name.startswith("codex_deepseek.")}
assert loaded <= ALLOWED
for control in ("codex_deepseek.browser", "codex_deepseek.image_provider"):
 try:
  __import__(control)
 except RuntimeError as error:
  assert str(error) == "Forbidden CDS runtime coupling: " + control
 else:
  raise AssertionError("Runtime boundary guard did not reject control import")
assert {name for name in sys.modules if name == "codex_deepseek" or name.startswith("codex_deepseek.")} <= ALLOWED
print(json.dumps({"pure_boundary":True}))
"""
    allowed={"codex_deepseek"+("."+name[:-3] if name!="__init__.py" else "") for name in PURE_HASHES}
    program="ALLOWED="+repr(allowed)+"\n"+program
    result=subprocess.run([sys.executable,"-I","-B","-c",program],cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={"pure_boundary":True}
