"""Offline exact-cache controls. Never run a compiler or change host policy."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

P = Path(__file__).with_name("p20-windows.py")
spec = importlib.util.spec_from_file_location("p21_gate", P)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class P21Gate(unittest.TestCase):
    def test_p21_preserves_p20_patches_and_native_profile(self):
        old, _, _ = ci.plan()
        with patch.multiple(ci, CANDIDATE="P21", LOCK=ci.P21_LOCK,
                            SOURCE=ci.P21_SOURCE, SOURCE_FILES=ci.P21_SOURCE_FILES):
            new, _, cases = ci.plan()
            self.assertEqual(new["patches"][:-1], old["patches"])
            self.assertEqual(new["release_environment"], old["release_environment"])
            self.assertEqual(new["binaries"], old["binaries"])
            self.assertEqual(len(cases), 18)
            self.assertEqual(len(ci.P21_GIT_CASES), 6)
            self.assertEqual(str(ci.work_path(Path("C:/fresh"))), r"C:\cagp20-42337939507-1\work")

    def test_cache_requires_hit_exact_toolchain_fresh_work_and_all_prior_bytes(self):
        env = {"CI_P20_CACHE_HIT": "true", "CI_P20_CACHE_KEY": ci.P20_WARM_CACHE_KEY,
               "VCToolsVersion": "14.44.35207", "UCRTVersion": "10.0.26100.0"}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            target = directory / "cache/target"
            root = directory / "evidence-root"
            (root / "evidence").mkdir(parents=True)
            (target / "release").mkdir(parents=True)
            files = {}
            for name in ci.BINS:
                data = name.encode()
                (target / "release" / name).write_bytes(data)
                files[name] = (len(data), hashlib.sha256(data).hexdigest())
            work = directory / "owned-previous/work"
            with patch.multiple(ci, P20_WARM_WORK=work, P20_CACHED_OUTPUTS=files), \
                    patch.object(ci, "cache_paths", return_value=(target.parent, target)):
                for bad in ({"CI_P20_CACHE_HIT": "false"}, {"CI_P20_CACHE_KEY": "other"},
                            {"VCToolsVersion": "other"}, {"UCRTVersion": "other"}):
                    with self.subTest(bad=bad), patch.dict(ci.os.environ, env | bad), \
                            self.assertRaises(ValueError):
                        ci.verify_p21_cache(root)
                with patch.dict(ci.os.environ, env):
                    work.parent.mkdir()
                    with self.assertRaises(ValueError):
                        ci.verify_p21_cache(root)
                    work.parent.rmdir()
                    p = target / "release/codex.exe"
                    original = p.read_bytes()
                    p.write_bytes(b"x" * len(original))
                    with self.assertRaises(ValueError):
                        ci.verify_p21_cache(root)
                    p.write_bytes(original)
                    ci.verify_p21_cache(root)
                    admitted = json.loads((root / "evidence/exact-p20-cache-admission.json").read_bytes())
                    self.assertEqual(admitted["cache_key"], ci.P20_WARM_CACHE_KEY)
                    self.assertEqual(admitted["original_source_path"], str(work / "source"))
                    self.assertEqual(admitted["old_output_sha256"], {k: v[1] for k, v in files.items()})


if __name__ == "__main__":
    unittest.main()
