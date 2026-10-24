from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
import pytest
from codex_app_deepseek import lifecycle as lc
from test_lifecycle import bundle, installed, save


@pytest.mark.parametrize("name", ["base", "venv", "config"])
def test_bootstrap_runtime_proof_rejects_actual_hardlinked_files(bundle, name):
    result, _ = installed(bundle)
    directory = Path(result["release_dir"])
    target = {"base": bundle.python, "venv": directory / "venv/Scripts/python.exe",
              "config": directory / "venv/pyvenv.cfg"}[name]
    os.link(target, bundle.root / (name + "-hardlink"))
    with pytest.raises(lc.LifecycleError, match="provenance"):
        lc._runtime_proof(directory, bundle.python)


@pytest.mark.skipif(sys.platform != "win32", reason="Real clean Windows base/bootstrap-wheel install")
def test_clean_base_installs_via_bootstrap_wheel_without_tomlkit(bundle):
    # The old lifecycle fixture carried tomlkit inside its wheel, masking a
    # production bootstrap dependency. This wheel deliberately contains none.
    with zipfile.ZipFile(bundle.wheel) as archive:
        members = {name: archive.read(name) for name in archive.namelist()
                   if not name.startswith("tomlkit/")}
    record = next(name for name in members if name.endswith(".dist-info/RECORD"))
    members[record] = "".join(name + ",,\n" for name in members).encode()
    with zipfile.ZipFile(bundle.wheel, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    assert not any(name.startswith("tomlkit/") for name in members)
    bundle.release["files"][0]["sha256"] = lc.digest(bundle.wheel)
    save(bundle.manifest, bundle.release)
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    code = r"""import importlib.util,runpy,sys
assert importlib.util.find_spec('tomlkit') is None, 'Bootstrap base unexpectedly includes third-party parser'
sys.path.insert(0,sys.argv[1])
sys.argv=['codex-app-deepseek-lifecycle',*sys.argv[2:]]
runpy.run_module('codex_app_deepseek.lifecycle',run_name='__main__')
"""
    result = subprocess.run(
        [str(base), "-I", "-c", code, str(bundle.wheel), "install",
         "--manifest", str(bundle.manifest), "--manifest-sha256", lc.digest(bundle.manifest),
         "--wheelhouse", str(bundle.wheels), "--python", str(base),
         "--install-root", str(bundle.install), "--data-home", str(bundle.data)],
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["status"] == "installed_not_activated"
    directory = Path(observed["release_dir"])
    receipt = lc._json(directory / "installation.json")
    assert receipt["runtime"] == lc._runtime_proof(directory, base)
    assert not (bundle.install / "active.json").exists() and not bundle.data.exists()
    assert not (directory / "venv/Lib/site-packages/tomlkit").exists()
