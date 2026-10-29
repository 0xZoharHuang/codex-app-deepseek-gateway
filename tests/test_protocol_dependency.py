"""Verify the installed fixed pure dependency and deny runtime import coupling."""
import hashlib
import importlib
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys
from codex_app_deepseek import gateway_policy

COMMIT="cb788738864e97a16cde82f3c584164162ab6f7c"
ARCHIVE="5168af937541a6f19158e42ff809b5cbcc4656b574fa478ef451805ee08bf7e4"
PURE_HASHES={'protocol.py': '58264666a72b3614348d3ccf74a094b0472a23e72d829237a4cf168b923af9ad', 'formats.py': '3f8f027b56731c30ec0ffb518da9a09671059b364785a8e39688fb1052ea8f47', 'structured.py': '8f54e9a5ad190bcfdd9d580adc00b184d5d440cd7376f5c5465a9a6c2e1c9d8a', 'protocol_errors.py': '446636771b39e39d36065a4ace549f4c5608d1f9005ff2723c75a573e1129452', 'search.py': '438a157d92e0c1e717fbfe7c9f7856df1edf38cf3d9a0d63188636e693500030', '__init__.py': '54a6b6d025f3e0b5213085e4aea4e383907e27cc46a03dcc35002b337e13e33e'}

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
    assert row["version"] == "0.6.7" and row["filename"] == "codex_deepseek_runtime-0.6.7-py3-none-any.whl"
    assert row["sha256"] == "762352fe471c3242dca0172d015a0d3cc86927b25aaa4312b36fad0148c7bb35"
    assert row["source"]["type"] == "github_release_wheel" and row["source"]["public_release_ready"] is True
    assert row["source"]["source_commit"] == COMMIT
    assert row["source"]["archive_url"] == expected_archive and row["source"]["archive_sha256"] == ARCHIVE
    assert row["source"]["artifact_url"] == "https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/download/v0.6.7/" + row["filename"]
    assert row["source"]["artifact_sha256"] == row["sha256"]
    assert lock["protocol_boundary"] == {"source_commit": COMMIT, "pure_files": PURE_HASHES}
    assert metadata.version("codex-deepseek-runtime")=="0.6.7"
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
