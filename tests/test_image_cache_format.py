"""App-only offline format boundary; no credentials, Files HTTP, App or native execution."""
from contextlib import closing, contextmanager
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest
from codex_app_deepseek import cli, image_cache_format as fmt, image_transport as image
from codex_deepseek import image_transport as core

LEGACY_SHA = "fe5449618c91a619ff56b7e879ca1665740cca4de5428a60e42b51970c950f94"

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class Backend:
    def __init__(self):
        self.owner = {"sid": "synthetic-current-owner", "session": 3, "elevated": False}
        self.alive = {}
        self.real = set()
        self.apps = []
        self.identities = {}
        self.environments = {}
    def current(self):
        return self.owner
    @contextmanager
    def launch_lock(self, sid):
        assert sid == self.owner["sid"]
        yield
    def existing_apps(self, owner):
        return self.apps
    def live(self, identity):
        if identity["pid"] in self.real:
            try:
                return psutil.Process(identity["pid"]).create_time() == identity["created"]
            except psutil.NoSuchProcess:
                return False
        return self.alive.get(identity["pid"], False)
    def identity(self, pid):
        if pid in self.real:
            p = psutil.Process(pid)
            return {"pid": pid, "created": p.create_time(), "sid": self.owner["sid"],
                    "session": 3, "elevated": False, "exe": p.exe(), "ppid": p.ppid()}
        return self.identities[pid]
    def selected_environment(self, identity):
        return self.environments[identity["pid"]]
    def profile_arguments(self, identity):
        return []

class Process:
    def __init__(self, pid, args, *, native=None, denied=False):
        self.pid, self.args, self.native, self.denied = pid, args, native, denied
    def name(self):
        return "codex.exe" if self.native else "python.exe"
    def exe(self):
        return str(self.native or Path(sys.executable))
    def cmdline(self):
        if self.denied:
            raise psutil.AccessDenied(self.pid)
        return self.args
    def environ(self):
        return {"LOCALAPPDATA": str(Path.cwd() / "unrelated-local")}
    def cwd(self):
        return str(Path.cwd())

@pytest.fixture
def boundary(tmp_path, monkeypatch):
    root, data = tmp_path / "managed", tmp_path / "data"
    root.mkdir(); data.mkdir()
    paths = {"home": tmp_path / "app-home", "frontend": tmp_path / "app-home" / "frontend",
             "launches": tmp_path / "launches", "native_logs": tmp_path / "logs"}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    native = tmp_path / "native" / "codex.exe"
    native.parent.mkdir(); native.write_bytes(b"not executable; actor path fixture only")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"outputs": [{"name": "codex.exe", "path": str(native)}]}))
    binding = {"native_manifest": str(manifest), "native_manifest_sha256": digest(manifest)}
    installation = SimpleNamespace(root=root, data=data, directory=root / "releases" / ("a" * 20),
                                   receipt={"release_id": "a" * 20})
    backend = Backend()
    writers = []
    monkeypatch.setattr(fmt.delivery, "installed", lambda selected: installation)
    monkeypatch.setattr(fmt.delivery, "read_binding", lambda selected, actor: binding)
    monkeypatch.setattr(fmt.app, "defaults", lambda: paths)
    monkeypatch.setattr(cli, "read", lambda *a: (_ for _ in ()).throw(AssertionError("credential read")))
    (data / "gateway.json").write_text(json.dumps({"image_transport": {"activate_above_bytes": 1}}))
    return SimpleNamespace(root=root, data=data, cache=data / "image-transport", paths=paths,
                           backend=backend, binding=binding, native=native, installation=installation,
                           processes=lambda: writers, writers=writers)

async def run(boundary, target=3, enroll=False):
    return await fmt.maintain(boundary.root, target, enroll_empty_legacy=enroll,
                              backend=boundary.backend, processes=boundary.processes)

def legacy_schema(path):
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.executescript("""
        CREATE TABLE assets(digest TEXT PRIMARY KEY,mime TEXT NOT NULL,nbytes INTEGER NOT NULL,
          filename TEXT NOT NULL,state TEXT NOT NULL,file_id TEXT,created REAL NOT NULL,
          last_used REAL NOT NULL,verified REAL NOT NULL,operation_owner TEXT,reason TEXT);
        CREATE TABLE pins(lease TEXT NOT NULL,digest TEXT NOT NULL,pid INTEGER NOT NULL,
          birth REAL NOT NULL,phase TEXT NOT NULL DEFAULT 'prepared',request_ref TEXT,
          PRIMARY KEY(lease,digest));
        CREATE TABLE receipts(sequence INTEGER PRIMARY KEY,at REAL NOT NULL,digest TEXT NOT NULL,
          event TEXT NOT NULL,detail TEXT);
        """)

def old_cache(b, names=("a" * 64,)):
    b.cache.mkdir()
    (b.cache / "scope.salt").write_bytes(b"s" * 32)
    for name in names:
        scope = b.cache / name; scope.mkdir()
        legacy_schema(scope / "files.sqlite")
    return [b.cache / name / "files.sqlite" for name in names]

def source_receipt(b, identities):
    run_dir = b.paths["launches"] / ("b" * 32); run_dir.mkdir()
    plan = {"validated": {"environment": {"CODEX_HOME": str(b.paths["home"])},
            "owner": {"sid": b.backend.owner["sid"]},
            "bundle": {"files": {"codex.exe": {"path": str(b.native)}}}}}
    (run_dir / "plan.json").write_text(json.dumps(plan))
    receipt = {"status": "started_expected_backend", "plan_sha256": digest(run_dir / "plan.json"),
               "native_processes": identities}
    (run_dir / "receipt.json").write_text(json.dumps(receipt))
    return run_dir

@pytest.mark.asyncio
async def test_fresh_keyless_format_and_true_complete(boundary):
    result = await run(boundary)
    assert result["complete"] is True and result["managed_gateway_retirement_verified"] is True
    assert result["App_actor_retirement_verified"] is True
    assert result["credentials_read"] is False
    assert result["external_HTTP_requests"] == result["inference_requests"] == 0
    assert (boundary.cache / "scope.salt").read_bytes() == core.V3_SALT_FENCE

@pytest.mark.asyncio
async def test_recorded_gateway_is_a_nonmutating_precondition(boundary):
    path = boundary.data / "service.json"; path.write_bytes(b'{"pid":123,"created":1}')
    before = path.read_bytes()
    with pytest.raises(fmt.ImageCacheFormatError, match="recorded gateway"):
        await run(boundary)
    assert path.read_bytes() == before and not boundary.cache.exists()
    assert not (boundary.root / "lifecycle.lock").exists()

@pytest.mark.asyncio
@pytest.mark.parametrize("alive", [True, None])
async def test_live_or_unknown_recorded_actor_rejects(boundary, alive):
    identity = {"pid": 4242, "created": 123.0, "sid": boundary.backend.owner["sid"]}
    source_receipt(boundary, [identity]); boundary.backend.alive[4242] = alive
    with pytest.raises(fmt.ImageCacheFormatError, match="live or unknown"):
        await run(boundary)
    assert not boundary.cache.exists()

@pytest.mark.asyncio
async def test_retired_recorded_actor_allows_without_adoption(boundary):
    source_receipt(boundary, [{"pid": 4242, "created": 123.0, "sid": boundary.backend.owner["sid"]}])
    result = await run(boundary)
    assert result["complete"] is True and result["retired_recorded_actors"] == 1

@pytest.mark.asyncio
async def test_foreign_receipt_rejects_without_changing_cache(boundary):
    source_receipt(boundary, [{"pid": 4242, "created": 123.0, "sid": "foreign"}])
    with pytest.raises(fmt.ImageCacheFormatError, match="different user"):
        await run(boundary)
    assert not boundary.cache.exists()

@pytest.mark.asyncio
async def test_standalone_cache_writer_rejects(boundary):
    p = Process(5050, ["python.exe", "-I", "-m", "codex_app_deepseek.cli",
                      "image-cache-cleanup", "--home", str(boundary.data)])
    boundary.writers.append(p)
    boundary.backend.identities[5050] = {"pid": 5050, "sid": boundary.backend.owner["sid"]}
    with pytest.raises(fmt.ImageCacheFormatError, match="standalone"):
        await run(boundary)
    assert not boundary.cache.exists()

@pytest.mark.asyncio
async def test_private_project_image_unreadable_selectors_rejects(boundary):
    p = Process(5050, ["python.exe", "-m", "codex_app_deepseek.cli"], denied=True)
    p.exe = lambda: str(boundary.root/"releases"/("a"*20)/"venv/Scripts/python.exe")
    boundary.writers.append(p)
    boundary.backend.identities[5050] = {"pid": 5050, "sid": boundary.backend.owner["sid"]}
    with pytest.raises(fmt.ImageCacheFormatError, match="unreadable command selectors"):
        await run(boundary)
    assert not boundary.cache.exists()

@pytest.mark.asyncio
async def test_disjoint_standalone_home_is_preserved(boundary):
    p = Process(5050, ["python.exe", "-m", "codex_app_deepseek.cli",
                      "image-cache-status", "--home", str(boundary.data.parent / "foreign")])
    boundary.writers.append(p)
    boundary.backend.identities[5050] = {"pid": 5050, "sid": boundary.backend.owner["sid"]}
    assert (await run(boundary))["complete"] is True

@pytest.mark.asyncio
async def test_orphan_native_with_dedicated_home_rejects(boundary):
    p = Process(6060, [], native=boundary.native); boundary.writers.append(p)
    identity = {"pid": 6060, "sid": boundary.backend.owner["sid"]}
    boundary.backend.identities[6060] = identity
    boundary.backend.environments[6060] = {"CODEX_HOME": str(boundary.paths["home"])}
    with pytest.raises(fmt.ImageCacheFormatError, match="native backend"):
        await run(boundary)
    assert not boundary.cache.exists()

@pytest.mark.asyncio
async def test_empty_legacy_needs_explicit_enrollment(boundary):
    files = old_cache(boundary)
    before = [p.read_bytes() for p in files]; salt = (boundary.cache / "scope.salt").read_bytes()
    with pytest.raises(fmt.ImageCacheFormatError, match="enroll-empty-legacy"):
        await run(boundary)
    assert before == [p.read_bytes() for p in files]
    assert salt == (boundary.cache / "scope.salt").read_bytes()

@pytest.mark.asyncio
async def test_empty_legacy_enrollment_and_rollback_keep_rows_and_salt(boundary):
    files = old_cache(boundary)
    salt = (boundary.cache / "scope.salt").read_bytes()
    result = await run(boundary, enroll=True)
    assert result["complete"] is True and len(result["explicit_empty_legacy_enrollment"]) == 1
    assert (boundary.cache / "scope.salt.v3-original").read_bytes() == salt
    assert (boundary.cache / "scope.salt").read_bytes() == core.V3_SALT_FENCE
    rollback = await run(boundary, target=2)
    assert rollback["complete"] is True
    assert (boundary.cache / "scope.salt").read_bytes() == salt
    for p in files:
        with closing(sqlite3.connect(p)) as db:
            assert db.execute("select schema_version from cache_identity").fetchone()[0] == 2
            assert all(db.execute("select count(*) from "+table).fetchone()[0] == 0
                       for table in ("assets", "pins", "receipts"))

@pytest.mark.asyncio
async def test_nonempty_unbound_legacy_is_never_enrolled(boundary):
    files = old_cache(boundary)
    with closing(sqlite3.connect(files[0], isolation_level=None)) as db:
        db.execute("insert into receipts values(NULL,0,'owned','historical',NULL)")
    before = files[0].read_bytes(); salt = (boundary.cache / "scope.salt").read_bytes()
    with pytest.raises(fmt.ImageCacheFormatError, match="Nonempty"):
        await run(boundary, enroll=True)
    assert files[0].read_bytes() == before and (boundary.cache / "scope.salt").read_bytes() == salt

@pytest.mark.asyncio
async def test_foreign_identity_in_second_scope_prevents_any_format_write(boundary):
    files = old_cache(boundary, ("a"*64, "b"*64))
    with closing(sqlite3.connect(files[1], isolation_level=None)) as db:
        db.execute("create table cache_identity(id INTEGER PRIMARY KEY CHECK(id=1),schema_version INTEGER NOT NULL,credential_scope TEXT NOT NULL,cache_root_sha256 TEXT NOT NULL)")
        db.execute("insert into cache_identity values(1,3,?,?)", ("b"*64, "0"*64))
    before = [p.read_bytes() for p in files]; salt = (boundary.cache/"scope.salt").read_bytes()
    with pytest.raises(image.ImageTransportError, match="ownership"):
        await run(boundary, enroll=True)
    assert before == [p.read_bytes() for p in files]
    assert (boundary.cache/"scope.salt").read_bytes() == salt
    assert not (boundary.cache/"cache-format.json").exists()

@pytest.mark.asyncio
async def test_started_cross_scope_failure_retains_transition_then_explicit_rollback(boundary, monkeypatch):
    files = old_cache(boundary, ("a"*64, "b"*64))
    original = core.ImageTransport._initialize_empty_ledger
    count = 0
    def fail_second(self, db, directory, version):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("owned injected second-scope identity write failure")
        return original(self, db, directory, version)
    monkeypatch.setattr(core.ImageTransport, "_initialize_empty_ledger", fail_second)
    result = await run(boundary, enroll=True)
    assert result["complete"] is False and result["status"] == "format_migration_incomplete"
    assert json.loads((boundary.cache/"cache-format.json").read_bytes())["state"] == "transition"
    assert (boundary.cache/"scope.salt.v3-original").read_bytes() == b"s"*32
    assert (boundary.cache/"scope.salt").read_bytes() == core.V3_SALT_FENCE
    for p in files:
        with closing(sqlite3.connect(p)) as db:
            assert all(db.execute("select count(*) from "+table).fetchone()[0] == 0
                       for table in ("assets", "pins", "receipts"))
    monkeypatch.setattr(core.ImageTransport, "_initialize_empty_ledger", original)
    rollback = await run(boundary, target=2, enroll=True)
    assert rollback["complete"] is True and (boundary.cache/"scope.salt").read_bytes() == b"s"*32

@pytest.mark.parametrize("complete,expected", [(True,0),(False,3),(1,2),(None,2)])
def test_cli_true_complete_is_required_without_credentials(complete, expected):
    # A standalone CLI owns asyncio.run's policy lifecycle. Calling it inside
    # pytest-asyncio's policy discards the plugin's restored old_loop; isolate
    # the genuine CLI process instead of suppressing that resource warning.
    program = """
import sys,json,gc
sys.path[:0]=sys.argv[1:4]
from codex_app_deepseek import cli,image_cache_format as fmt
complete=json.loads(sys.argv[4])
async def fake(*args,**kwargs):return {'complete':complete,'credentials_read':False}
def forbidden(*args):raise AssertionError('credential read')
fmt.maintain=fake;cli.read=forbidden
code=cli.main(['image-cache-format','--install-root','owned-root','--target-version','3'])
gc.collect()
raise SystemExit(code)
"""
    result=subprocess.run([sys.executable,"-I","-B","-W","error","-c",program,
        str(Path(image.__file__).resolve().parents[1]),
        str(Path(core.__file__).resolve().parents[1]),
        str(Path(psutil.__file__).resolve().parents[1]),json.dumps(complete)],
        capture_output=True,timeout=20)
    assert result.returncode==expected,result.stderr
    assert b"ResourceWarning" not in result.stderr and b"Traceback" not in result.stderr
    if type(complete) is bool:
        assert json.loads(result.stdout)["complete"] is complete
        assert not result.stderr
    else:
        assert b"no definite completion status" in result.stderr

@pytest.mark.skipif(os.name != "nt", reason="actual Windows App legacy reader and byte-lock gate")
@pytest.mark.asyncio
async def test_real_cached_legacy_actor_is_rejected_at_locked_prewrite_boundary(boundary, monkeypatch, tmp_path):
    fixture = Path(__file__).parent / "fixtures/legacy_app_image_transport_v2.py"
    assert digest(fixture) == LEGACY_SHA
    boundary.backend.owner["sid"] = fmt.app.WindowsBackend().current()["sid"]
    ready, release = tmp_path/"reader-ready.json", tmp_path/"reader-release"
    program = """
import sys,asyncio,importlib.util,json,time
from pathlib import Path
sys.path[:0]=sys.argv[1:4]
spec=importlib.util.spec_from_file_location('codex_app_deepseek._legacy_v2_test',sys.argv[4])
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
root=Path(sys.argv[5]);ready=Path(sys.argv[6]);release=Path(sys.argv[7])
t=m.ImageTransport(root,lambda:'synthetic-never-read',None,m.TransportPolicy(1))
scope,directory=asyncio.run(t._scope('synthetic-not-a-provider-key'))
ready.write_text(json.dumps({'scope':scope,'directory':str(directory)}))
end=time.monotonic()+20
while not release.exists() and time.monotonic()<end:time.sleep(.01)
assert release.exists(),'owned parent did not release reader'
"""
    with (tmp_path/"reader.out").open("wb") as out, (tmp_path/"reader.err").open("wb") as err:
        child = subprocess.Popen([sys.executable,"-I","-B","-c",program,
            str(Path(image.__file__).resolve().parents[1]),
            str(Path(core.__file__).resolve().parents[1]),
            str(Path(psutil.__file__).resolve().parents[1]), str(fixture),
            str(boundary.cache),str(ready),str(release)], stdout=out,stderr=err)
        try:
            created = psutil.Process(child.pid).create_time()
            assert psutil.Process(child.pid).username() == psutil.Process().username()
            boundary.backend.real.add(child.pid)
            end=time.monotonic()+10
            while not ready.exists() and child.poll() is None and time.monotonic()<end:
                time.sleep(.01)
            assert ready.exists(),(tmp_path/"reader.err").read_bytes()
            cached=json.loads(ready.read_bytes());scope=Path(cached["directory"])
            legacy_schema(scope/"files.sqlite")
            second=boundary.cache/("f"*64);second.mkdir();legacy_schema(second/"files.sqlite")
            locks=[boundary.cache/"initialize.lock",scope/"cache.lock",second/"cache.lock"]
            for folder in (scope,second):
                lock=folder/("asset-"+("1"*64)+".lock");lock.write_bytes(b"\0");locks.append(lock)
            before={str(p):p.read_bytes()for p in (boundary.cache/"scope.salt",scope/"files.sqlite",second/"files.sqlite")}
            real_guard=fmt._guard;calls=0;lock_proof=[]
            def guard(*args):
                nonlocal calls
                calls+=1
                if calls==3:
                    # All these locks must already be held by the shared migrate
                    # API. This independent process uses actual Win32 byte locks.
                    probe="import sys,json,msvcrt;out=[]\nfor name in sys.argv[1:]:\n f=open(name,'r+b');f.seek(0)\n try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)\n except OSError:out.append(True)\n else:out.append(False);f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)\n f.close()\nprint(json.dumps(out))"
                    result=subprocess.run([sys.executable,"-I","-B","-c",probe,*map(str,locks)],capture_output=True,timeout=10)
                    assert result.returncode==0,result.stderr
                    lock_proof.extend(json.loads(result.stdout));assert all(lock_proof)
                    source_receipt(boundary,[{"pid":child.pid,"created":created,"sid":boundary.backend.owner["sid"]}])
                return real_guard(*args)
            monkeypatch.setattr(fmt,"_guard",guard)
            with pytest.raises(fmt.ImageCacheFormatError,match="live or unknown"):
                await run(boundary,enroll=True)
            assert len(lock_proof)==5 and all(lock_proof)
            assert before=={name:Path(name).read_bytes()for name in before}
            assert not(boundary.cache/"cache-format.json").exists()
            assert not(boundary.cache/"scope.salt.v3-original").exists()
        finally:
            release.write_bytes(b"release")
            assert child.wait(timeout=10)==0,(tmp_path/"reader.err").read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("same_profile", [False, True])
async def test_existing_app_is_scoped_to_its_actual_profile(boundary, same_profile):
    ident = {"pid": 7070, "created": 77.0, "sid": boundary.backend.owner["sid"],
             "session": 3, "exe": r"C:\WindowsApps\OpenAI.Codex_test\ChatGPT.exe"}
    boundary.backend.apps.append(ident); boundary.backend.identities[7070] = ident
    boundary.backend.alive[7070] = True
    env = dict.fromkeys(fmt.app.CONTROLLED)
    env["CODEX_HOME"] = str(boundary.paths["home"] if same_profile else boundary.data.parent/"other-app-home")
    boundary.backend.environments[7070] = env
    before = json.dumps(ident, sort_keys=True)
    if same_profile:
        with pytest.raises(fmt.ImageCacheFormatError):
            await run(boundary)
        assert not boundary.cache.exists()
    else:
        assert (await run(boundary))["complete"] is True
    assert json.dumps(ident, sort_keys=True) == before

@pytest.mark.asyncio
async def test_unobserved_admitted_app_launch_is_not_assumed_retired(boundary):
    run_dir = boundary.paths["launches"]/("c"*32); run_dir.mkdir()
    (run_dir/"plan.json").write_text(json.dumps({"validated":{
        "owner":{"sid":boundary.backend.owner["sid"]},
        "environment":{"CODEX_HOME":str(boundary.paths["home"])}}}))
    with pytest.raises(fmt.ImageCacheFormatError, match="no terminal receipt"):
        await run(boundary)
    assert not boundary.cache.exists()

@pytest.mark.asyncio
async def test_first_marker_replace_failure_is_atomic_and_not_complete(boundary, monkeypatch):
    files = old_cache(boundary, ("a"*64,"b"*64))
    before = {str(p):p.read_bytes() for p in [boundary.cache/"scope.salt",*files]}
    def fail_publish(*args):
        raise OSError("owned injected metadata publication failure")
    monkeypatch.setattr(core,"_replace_private_state",fail_publish)
    result = await run(boundary,enroll=True)
    assert result["complete"] is False and result["status"]=="format_migration_incomplete"
    assert before == {name:Path(name).read_bytes() for name in before}
    assert not(boundary.cache/"cache-format.json").exists()
    assert not(boundary.cache/"scope.salt.v3-original").exists()
    assert not list(boundary.cache.glob(".cache-format-*"))

@pytest.mark.skipif(os.name != "nt", reason="real Windows service/launch locks")
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["service","launch"])
async def test_real_existing_lifecycle_barriers_prevent_format_change(boundary, tmp_path, kind):
    ready,release=tmp_path/"barrier-ready",tmp_path/"barrier-release"
    # Use a unique synthetic SID mutex, so the real user's App is unaffected.
    sid="owned-fixture-"+hashlib.sha256(str(tmp_path).encode()).hexdigest()
    boundary.backend.owner["sid"]=sid
    boundary.backend.launch_lock=fmt.app.WindowsBackend().launch_lock
    program="""
import sys,time
from pathlib import Path
sys.path[:0]=sys.argv[1:4]
from codex_app_deepseek.cli import _service_lease
from codex_app_deepseek.isolated_app import WindowsBackend
kind,target,sid=sys.argv[4:7];ready=Path(sys.argv[7]);release=Path(sys.argv[8])
guard=_service_lease(Path(target)) if kind=='service' else WindowsBackend().launch_lock(sid)
with guard:
 ready.write_bytes(b'held');end=time.monotonic()+15
 while not release.exists() and time.monotonic()<end:time.sleep(.01)
 assert release.exists()
"""
    with (tmp_path/"barrier.out").open("wb")as out,(tmp_path/"barrier.err").open("wb")as err:
        child=subprocess.Popen([sys.executable,"-I","-B","-W","error","-c",program,
            str(Path(image.__file__).resolve().parents[1]),
            str(Path(core.__file__).resolve().parents[1]),
            str(Path(psutil.__file__).resolve().parents[1]),
            kind,str(boundary.data),sid,str(ready),str(release)],stdout=out,stderr=err)
        try:
            birth=psutil.Process(child.pid).create_time()
            assert psutil.Process(child.pid).username()==psutil.Process().username()
            end=time.monotonic()+10
            while not ready.exists() and child.poll() is None and time.monotonic()<end:time.sleep(.01)
            assert ready.exists(),(tmp_path/"barrier.err").read_bytes()
            assert psutil.Process(child.pid).create_time()==birth
            with pytest.raises(fmt.ImageCacheFormatError):
                await run(boundary)
            assert not boundary.cache.exists()
        finally:
            release.write_bytes(b'release')
            assert child.wait(timeout=10)==0,(tmp_path/"barrier.err").read_bytes()


def deny_identity(*args):
    raise AssertionError("Unattributed/disjoint processes must not trigger owner inspection")

@pytest.mark.asyncio
async def test_nonmatching_readable_python_skips_identity_inspection(boundary, monkeypatch):
    boundary.writers.append(Process(5050, ["python.exe", "unrelated_worker.py"]))
    monkeypatch.setattr(boundary.backend, "identity", deny_identity)
    assert (await run(boundary))["complete"] is True

@pytest.mark.asyncio
async def test_disjoint_cli_with_denied_identity_is_not_our_writer(boundary, monkeypatch):
    boundary.writers.append(Process(5050, ["python.exe","-m","codex_app_deepseek.cli",
        "image-cache-cleanup","--home",str(boundary.data.parent/"other-data")]))
    monkeypatch.setattr(boundary.backend, "identity", deny_identity)
    assert (await run(boundary))["complete"] is True

@pytest.mark.asyncio
async def test_matching_cli_unknown_identity_still_rejects(boundary, monkeypatch):
    boundary.writers.append(Process(5050, ["python.exe","-m","codex_app_deepseek.cli",
        "image-cache-cleanup","--home",str(boundary.data)]))
    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(boundary.backend, "identity", denied)
    with pytest.raises(fmt.ImageCacheFormatError, match="source-matched"):
        await run(boundary)
    assert not boundary.cache.exists()

@pytest.mark.asyncio
async def test_matching_native_path_unknown_identity_still_rejects(boundary, monkeypatch):
    boundary.writers.append(Process(5050, [], native=boundary.native))
    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(boundary.backend, "identity", denied)
    with pytest.raises(fmt.ImageCacheFormatError, match="source-matched"):
        await run(boundary)
    assert not boundary.cache.exists()

@pytest.mark.asyncio
async def test_unattributed_native_basename_does_not_block_shared_machine(boundary, monkeypatch):
    p=Process(5050, [], native=Path("Z:/unrelated/codex.exe"))
    def denied():
        raise psutil.AccessDenied(p.pid)
    p.exe=denied;boundary.writers.append(p)
    monkeypatch.setattr(boundary.backend, "identity", deny_identity)
    assert (await run(boundary))["complete"] is True

@pytest.mark.asyncio
async def test_real_nonmatching_pid_with_interface_denial_is_not_adopted(boundary, monkeypatch, tmp_path):
    # This is a real owned PID/lifetime with an injected observation failure,
    # not an OS-DACL denial and not a claim to inspect arbitrary Python code.
    ready,release=tmp_path/"unrelated-ready",tmp_path/"unrelated-release"
    program="import sys,time;from pathlib import Path;r=Path(sys.argv[1]);done=Path(sys.argv[2]);r.write_bytes(b'ready');end=time.monotonic()+15\nwhile not done.exists() and time.monotonic()<end:time.sleep(.01)\nassert done.exists()"
    with (tmp_path/"unrelated.out").open("wb")as out,(tmp_path/"unrelated.err").open("wb")as err:
        child=subprocess.Popen([sys.executable,"-I","-B","-W","error","-c",program,
            str(ready),str(release)],stdout=out,stderr=err)
        try:
            observed=psutil.Process(child.pid);birth=observed.create_time()
            assert observed.username()==psutil.Process().username()
            end=time.monotonic()+10
            while not ready.exists() and child.poll() is None and time.monotonic()<end:time.sleep(.01)
            assert ready.exists(),(tmp_path/"unrelated.err").read_bytes()
            class DeniedView:
                pid=child.pid
                def name(self):return observed.name()
                def exe(self):return observed.exe()
                def cmdline(self):raise psutil.AccessDenied(self.pid)
            view=DeniedView()
            assert not fmt._private_python_image(view,boundary.installation,boundary.paths)
            boundary.writers.append(view)
            monkeypatch.setattr(boundary.backend,"identity",deny_identity)
            result=await run(boundary)
            assert result["complete"] is True
            assert child.poll() is None and psutil.Process(child.pid).create_time()==birth
        finally:
            release.write_bytes(b"release")
            assert child.wait(timeout=10)==0,(tmp_path/"unrelated.err").read_bytes()


def _probe_held_locks(paths):
    program="""
import sys,os,json
out=[]
for name in sys.argv[1:]:
 with open(name,'r+b') as f:
  f.seek(0)
  try:
   if os.name=='nt':
    import msvcrt;msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
   else:
    import fcntl;fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except OSError:out.append(True)
  else:
   out.append(False)
   if os.name=='nt':f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
   else:fcntl.flock(f,fcntl.LOCK_UN)
print(json.dumps(out))
"""
    q=subprocess.run([sys.executable,"-I","-B","-W","error","-c",program,
        *map(str,paths)],capture_output=True,timeout=20)
    assert q.returncode==0 and not q.stderr,q.stderr
    return json.loads(q.stdout)

@pytest.mark.asyncio
async def test_fresh_first_salt_publication_is_after_locked_actor_recheck(boundary,monkeypatch):
    real_guard=fmt._guard;observed=[];locks=[]
    def guard(*args):
        observed.append({"salt":(boundary.cache/"scope.salt").exists(),
                         "marker":(boundary.cache/"cache-format.json").exists()})
        if len(observed)==3:
            locks.extend(_probe_held_locks([boundary.cache/"initialize.lock"]))
            raise fmt.ImageCacheFormatError("owned injected first-publication retirement refusal")
        return real_guard(*args)
    monkeypatch.setattr(fmt,"_guard",guard)
    try:
        result=await run(boundary)
    except fmt.ImageCacheFormatError:
        result=None
    assert result is None or result["complete"] is False
    assert locks==[True]
    assert observed[2]=={"salt":False,"marker":False}
    assert not(boundary.cache/"scope.salt").exists()
    assert not(boundary.cache/"scope.salt.v3-original").exists()
    assert not(boundary.cache/"cache-format.json").exists()

def _finished_legacy_reader_creates_empty_tables(boundary):
    fixture=Path(__file__).parent/"fixtures/legacy_app_image_transport_v2.py"
    assert digest(fixture)==LEGACY_SHA
    program="""
import sys,asyncio,importlib.util,json,os
from pathlib import Path
sys.path[:0]=sys.argv[1:4]
import psutil
spec=importlib.util.spec_from_file_location('codex_app_deepseek._legacy_r579_test',sys.argv[4])
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
t=m.ImageTransport(Path(sys.argv[5]),lambda:'synthetic-never-read',None,m.TransportPolicy(1))
async def work():
 scope,directory=await t._scope('synthetic-maintenance-race')
 async with t._db(directory) as db:
  tables=sorted(r[0]for r in db.execute("select name from sqlite_master where type='table'"))
  counts={name:db.execute('select count(*) from '+name).fetchone()[0]for name in tables}
 return {'scope':scope,'directory':str(directory),'tables':tables,'counts':counts,
         'pid':os.getpid(),'birth':psutil.Process().create_time()}
print(json.dumps(asyncio.run(work())))
"""
    q=subprocess.run([sys.executable,"-I","-B","-W","error","-c",program,
        str(Path(image.__file__).resolve().parents[1]),
        str(Path(core.__file__).resolve().parents[1]),
        str(Path(psutil.__file__).resolve().parents[1]),str(fixture),str(boundary.cache)],
        capture_output=True,timeout=20)
    assert q.returncode==0 and not q.stderr,q.stderr
    result=json.loads(q.stdout)
    assert result["tables"]==["assets","pins","receipts"]
    assert set(result["counts"].values())=={0}
    try:
        assert psutil.Process(result["pid"]).create_time()!=result["birth"]
    except psutil.NoSuchProcess:
        pass
    result["snapshot"]={str(p):p.read_bytes()for p in
        [boundary.cache/"scope.salt",Path(result["directory"])/"files.sqlite"]}
    return result

@pytest.mark.asyncio
@pytest.mark.parametrize("allowed",[False,True])
async def test_late_old_legacy_enrollment_requires_flag_and_reports_actual_scope(boundary,monkeypatch,allowed):
    original=fmt._legacy_enrollment;state={};first=True
    def precheck_then_finish_old_reader(cache,flag):
        nonlocal first
        result=original(cache,flag)
        if first:
            first=False
            assert result==[] and not cache.exists()
            state.update(_finished_legacy_reader_creates_empty_tables(boundary))
        return result
    monkeypatch.setattr(fmt,"_legacy_enrollment",precheck_then_finish_old_reader)
    if allowed:
        result=await run(boundary,enroll=True)
        assert result["complete"] is True
        assert result["explicit_empty_legacy_enrollment"]==[state["scope"]]
        assert (boundary.cache/"scope.salt").read_bytes()==core.V3_SALT_FENCE
    else:
        rejected=False
        try:
            result=await run(boundary)
        except fmt.ImageCacheFormatError:
            rejected=True
        else:
            rejected=result["complete"] is False
        assert rejected,"A completed old reader cannot bypass explicit empty enrollment"
        assert state["snapshot"]=={name:Path(name).read_bytes()for name in state["snapshot"]}
        assert not(boundary.cache/"cache-format.json").exists()
        assert not(boundary.cache/"scope.salt.v3-original").exists()
