"""Alias wiring unit tests: mocked package admission/CLI; no native process, App or deployment."""
from contextlib import nullcontext
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from codex_app_deepseek import app_delivery as delivery
from codex_app_deepseek import isolated_app as app
from codex_app_deepseek import native_app_tools as tools
from codex_app_deepseek import runtime_package as packages
from test_isolated_app import fixture as native_fixture
from test_app_delivery import fixture as delivery_fixture, configure, healthy_gateway
from test_native_app_tools import fixture as tools_fixture, FakeNativeCLI, bind


def package_context(tmp_path, native, mode="registered_core"):
    alias_name = packages.MODES[mode][1]
    alias = tmp_path/"aliases/CADSG.NativeRuntime_123456789abcd"/alias_name
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.write_bytes(b"unit alias locator never executed")
    descriptor = tmp_path/("descriptor-"+mode+".json")
    descriptor.write_text(json.dumps({"kind":packages.KIND}))
    return {"mode":mode,"application_id":packages.MODES[mode][0],
            "alias_path":str(alias),"alias_name":alias_name,
            "install_location":str(native.parent.parent),
            "package":{"family":"CADSG.NativeRuntime_123456789abcd"},
            "descriptor_path":str(descriptor),"descriptor_sha256":app.sha(descriptor)}


@pytest.mark.parametrize("mode", ["registered_core", "legacy"])
def test_native_tools_verify_physical_bytes_but_invoke_verified_package_alias(tools_fixture,tmp_path,monkeypatch,mode):
    _,home,_,_,_,_=tools_fixture
    native=tmp_path/"installed-package/runtime/codex.exe"
    native.parent.mkdir(parents=True);native.write_bytes(b"unit physical native never executed")
    context=package_context(tmp_path,native,mode)
    admitted={"files":{"codex.exe":{"path":str(native),"sha256":app.sha(native)}},"runtime_package":context}
    calls=[];spawned={}
    monkeypatch.setattr(app,"baseline",lambda path:{"unit":"fixed App baseline"})
    monkeypatch.setattr(app,"WindowsBackend",lambda:SimpleNamespace(current=lambda:{"sid":"sid"}))
    def verify(path,digest,expected,backend,owner,actual_home):
        calls.append((path,digest,owner,actual_home))
        assert path==context["descriptor_path"] and digest==context["descriptor_sha256"]
        assert owner["sid"]=="sid" and Path(actual_home)==home
        return copy.deepcopy(admitted)
    monkeypatch.setattr(app,"verify_launch_bundle",verify)
    class Process:
        pid=12345;returncode=0
        def poll(self):return None
        def communicate(self,timeout=None):return b"{}",b""
    def spawn(argv,**kwargs):
        spawned.update(argv=argv,environment=kwargs["env"])
        return Process()
    monkeypatch.setattr(tools.subprocess,"Popen",spawn)
    monkeypatch.setattr(__import__("psutil"),"Process",lambda pid:SimpleNamespace(create_time=lambda:1.0))
    receipt={"owner_sid":"sid"}
    cli=tools.NativeCLI(native,app.sha(native),home,receipt,None,runtime_package=context)
    assert cli.call(["mcp","get",tools.SERVER,"--json"])=={}
    assert len(calls)==2
    assert spawned["argv"][0]==context["alias_path"] and spawned["argv"][0]!=str(native)
    assert spawned["environment"]["CODEX_HOME"]==str(home)
    assert spawned["environment"]["CODEX_WINDOWS_REGISTERED_CORE"]==("1" if mode=="registered_core" else "0")
    assert 'cli_auth_credentials_store="file"' in spawned["argv"]
    native.write_bytes(b"tampered")
    monkeypatch.setattr(tools.subprocess,"Popen",lambda *a,**k:pytest.fail("No child after physical tampering"))
    with pytest.raises(ValueError,match="changed"):cli.call(["mcp","get",tools.SERVER,"--json"])


@pytest.mark.parametrize("fault", ["missing-descriptor","foreign-owner","changed-context"])
def test_packaged_native_tools_cannot_launch_unadmitted_alias(tools_fixture,tmp_path,monkeypatch,fault):
    _,home,_,_,_,_=tools_fixture
    native=tmp_path/"installed-package/runtime/codex.exe";native.parent.mkdir(parents=True)
    native.write_bytes(b"unit native never executed")
    context=package_context(tmp_path,native)
    if fault=="missing-descriptor":context.pop("descriptor_sha256")
    monkeypatch.setattr(app,"baseline",lambda path:{})
    monkeypatch.setattr(app,"WindowsBackend",lambda:SimpleNamespace(current=lambda:{"sid":"other" if fault=="foreign-owner" else "sid"}))
    admitted_context=copy.deepcopy(context)
    if fault=="changed-context":admitted_context["mode"]="legacy"
    monkeypatch.setattr(app,"verify_launch_bundle",lambda *args:{"files":{"codex.exe":{"path":str(native),"sha256":app.sha(native)}},"runtime_package":admitted_context})
    monkeypatch.setattr(tools.subprocess,"Popen",lambda *a,**k:pytest.fail("No child before descriptor admission"))
    with pytest.raises(ValueError):
        tools.NativeCLI(native,app.sha(native),home,{"owner_sid":"sid"},None,runtime_package=context)


def package_runner(expected):
    class Runner(FakeNativeCLI):
        events=[];fail=None;contexts=[]
        def __init__(self, executable,checksum,home,receipt,receipt_path,*,runtime_package=None):
            assert runtime_package==expected
            type(self).contexts.append(copy.deepcopy(runtime_package))
            super().__init__(executable,checksum,home,receipt,receipt_path)
    return Runner


def test_bootstrap_configured_and_private_rollback_keep_explicit_core_context(tools_fixture,tmp_path):
    package,home,data,catalog,native,inventory=tools_fixture
    bind(tools_fixture)
    context=package_context(tmp_path,native)
    Runner=package_runner(context)
    receipt=data/"installation.json"
    result=tools.bootstrap(home,native,tools._sha(native),inventory,receipt,owner_sid="sid",
        catalog_path=catalog,catalog_sha256=tools._sha(catalog),backup_dir=data/"policy",
        source_root=package/"bundled",runner_factory=Runner,runtime_package=context,
        native_manifest=context["descriptor_path"],native_manifest_sha256=context["descriptor_sha256"])
    assert result["runtime_package"]==context and result["native"]==str(native)
    assert tools.configured(home,native,tools._sha(native),inventory,receipt,owner_sid="sid",
        runner_factory=Runner,runtime_package=context)["status"]=="native_plugin_configuration_verified"
    with pytest.raises(ValueError,match="explicit verified package"):
        tools.configured(home,native,tools._sha(native),inventory,receipt,owner_sid="sid",
                         runner_factory=FakeNativeCLI)
    changed={**context,"mode":"legacy"}
    with pytest.raises(ValueError,match="activation differs"):
        tools.configured(home,native,tools._sha(native),inventory,receipt,owner_sid="sid",
                         runner_factory=package_runner(changed),runtime_package=changed)
    removed=tools.rollback(home,inventory,receipt,owner_sid="sid",runner_factory=Runner,runtime_package=context)
    assert removed["status"]=="removed" and removed["runtime_package_retained"] is True
    assert removed["native_package_teardown_performed"] is False
    assert Runner.contexts and all(value==context for value in Runner.contexts)


def test_configured_rebind_checks_new_context_before_publishing_it(tools_fixture,tmp_path):
    package,home,data,catalog,native,inventory=tools_fixture
    bind(tools_fixture)
    old=package_context(tmp_path,native,"legacy")
    NewRunner=package_runner(old)
    receipt=data/"installation.json"
    tools.bootstrap(home,native,tools._sha(native),inventory,receipt,owner_sid="sid",
        catalog_path=catalog,catalog_sha256=tools._sha(catalog),backup_dir=data/"policy",
        source_root=package/"bundled",runner_factory=NewRunner,runtime_package=old,
        native_manifest=old["descriptor_path"],native_manifest_sha256=old["descriptor_sha256"])
    current=package_context(tmp_path,native,"registered_core")
    class Observe(package_runner(current)):
        def call(self,args):
            persisted=json.loads(receipt.read_bytes())
            assert persisted["runtime_package"]==old
            assert args==["mcp","get",tools.SERVER,"--json"]
            return super().call(args)
    result=tools.bootstrap(home,native,tools._sha(native),inventory,receipt,owner_sid="sid",
        catalog_path=catalog,catalog_sha256=tools._sha(catalog),backup_dir=data/"policy",
        source_root=package/"bundled",runner_factory=Observe,runtime_package=current,
        native_manifest=current["descriptor_path"],native_manifest_sha256=current["descriptor_sha256"])
    assert result["runtime_package"]==current
    assert json.loads(receipt.read_bytes())["native_manifest"]==current["descriptor_path"]


def test_delivery_bind_configure_and_check_pass_same_verified_runtime(delivery_fixture,tmp_path,monkeypatch):
    f=delivery_fixture
    physical=json.loads(f.manifest.read_bytes())["outputs"][0]
    native=Path(physical["path"])
    context=package_context(tmp_path,native)
    admitted={"files":{"codex.exe":physical},"runtime_package":context}
    checks=[]
    def verify(path,digest,expected,backend,owner,home):
        checks.append((path,digest,owner,home))
        assert owner["sid"]==f.backend.current()["sid"] and Path(home)==f.paths["home"]
        return copy.deepcopy(admitted)
    monkeypatch.setattr(app,"verify_launch_bundle",verify)
    monkeypatch.setattr(app,"verify_bundle",lambda *a,**k:pytest.fail("New admission helper must be used"))
    observed=[]
    monkeypatch.setattr(tools,"bootstrap",lambda *a,**kw:observed.append(("bootstrap",a,kw)) or {"state":"configured"})
    monkeypatch.setattr(tools,"configured",lambda *a,**kw:observed.append(("configured",a,kw)) or {"status":"unit"})
    configure(f)
    monkeypatch.setattr(delivery,"owned_gateway",lambda unused:healthy_gateway(f))
    assert delivery.check(f.installation.root,backend=f.backend)["status"]=="validated_not_launched"
    assert len(checks)>=3
    assert [row[0] for row in observed]==["bootstrap","configured"]
    for kind,args,kwargs in observed:
        assert args[1]==physical["path"] and args[2]==physical["sha256"]
        assert kwargs["runtime_package"]==context
    assert not f.backend.spawned


def test_rollback_admission_revalidates_packaged_descriptor_and_passes_context(tools_fixture,tmp_path,monkeypatch):
    package,home,data,catalog,native,inventory=tools_fixture
    receipt=tools.receipt_paths(data)[1];receipt.parent.mkdir(parents=True)
    context=package_context(tmp_path,native)
    value={"owner_sid":"sid","home":str(home),"native":str(native),"native_sha256":tools._sha(native),
           "native_manifest":context["descriptor_path"],"native_manifest_sha256":context["descriptor_sha256"]}
    receipt.write_text(json.dumps(value))
    backend=SimpleNamespace(current=lambda:{"sid":"sid"},launch_lock=lambda sid:nullcontext(),
                            home_guard=lambda *a,**k:nullcontext())
    monkeypatch.setattr(app,"defaults",lambda:{"home":home})
    monkeypatch.setattr(app,"validate_state_paths",lambda paths:None)
    monkeypatch.setattr(app,"require_disjoint_apps",lambda *args:None)
    monkeypatch.setattr(app,"baseline",lambda path:{})
    monkeypatch.setattr(app,"verify_bundle",lambda *a,**k:pytest.fail("No legacy cleanup-only admission"))
    monkeypatch.setattr(app,"verify_launch_bundle",lambda *args:{"files":{"codex.exe":{"path":str(native),"sha256":tools._sha(native)}},"runtime_package":context})
    observed={}
    monkeypatch.setattr(tools,"rollback",lambda *a,**k:observed.update(k) or {"status":"removed"})
    with tools.rollback_admission(data,backend=backend) as rollback:
        assert rollback()["status"]=="removed"
    assert observed["runtime_package"]==context and observed["owner_sid"]=="sid"


@pytest.mark.parametrize("mode", [None, "legacy"])
def test_explicit_bind_cannot_bypass_owned_core_teardown(delivery_fixture,tmp_path,monkeypatch,mode):
    f=delivery_fixture
    physical=json.loads(f.manifest.read_bytes())["outputs"][0]
    context=package_context(tmp_path,Path(physical["path"]),mode) if mode else None
    admitted={"files":{"codex.exe":physical}}
    if context is not None:admitted["runtime_package"]=context
    monkeypatch.setattr(app,"verify_launch_bundle",lambda *args:copy.deepcopy(admitted))
    f.backend.native_runtime_owner_snapshot=lambda:{"state_absent":False,"runtime_present":True,
        "owner_sid":f.backend.current()["sid"],"home":str(f.paths["home"])}
    with pytest.raises(ValueError,match="native package teardown"):
        delivery.bind(f.installation.root,f.manifest,app.sha(f.manifest),backend=f.backend)
    assert not delivery.binding_path(f.installation).exists() and not f.backend.spawned


@pytest.mark.parametrize("target,registered_home,registered_sid", [
    ("registered_core","same","same"),
    ("legacy","other","same"),
    (None,"hidden","other"),
])
def test_explicit_bind_honors_current_native_owner_home_scope(delivery_fixture,tmp_path,monkeypatch,target,registered_home,registered_sid):
    f=delivery_fixture
    physical=json.loads(f.manifest.read_bytes())["outputs"][0]
    context=package_context(tmp_path,Path(physical["path"]),target) if target else None
    admitted={"files":{"codex.exe":physical}}
    if context is not None:admitted["runtime_package"]=context
    monkeypatch.setattr(app,"verify_launch_bundle",lambda *args:copy.deepcopy(admitted))
    snapshot={"state_absent":False,"runtime_present":True,
        "owner_sid":f.backend.current()["sid"] if registered_sid=="same" else None,
        "home":str(f.paths["home"] if registered_home=="same" else tmp_path/"other-home") if registered_home!="hidden" else None}
    f.backend.native_runtime_owner_snapshot=lambda:snapshot
    result=delivery.bind(f.installation.root,f.manifest,app.sha(f.manifest),backend=f.backend)
    assert result["status"]=="runtime_bound_not_launched"
    assert delivery.binding_path(f.installation).exists() and not f.backend.spawned


def test_msix_prepare_binds_fresh_home_resources_without_copying_stock_host(native_fixture,tmp_path,monkeypatch):
    paths,expected,legacy_manifest,legacy_document,request,backend=native_fixture
    native=tmp_path/"own-installed/runtime/codex.exe"
    native.parent.mkdir(parents=True);native.write_bytes(b"own physical CLI fixture, never executed")
    host=native.with_name("codex-code-mode-host.exe")
    host.write_bytes(b"own host must never be overwritten with stock closed host")
    context=package_context(tmp_path,native)
    descriptor=Path(context["descriptor_path"]);descriptor_before=descriptor.read_bytes()
    admitted={"manifest":str(descriptor),"manifest_sha256":app.sha(descriptor),
              "files":{"codex.exe":{"path":str(native),"sha256":app.sha(native)}},"runtime_package":context}
    calls=[]
    def verify(path,digest,baseline,actual_backend,owner,home):
        assert Path(path)==descriptor and digest==app.sha(descriptor)
        assert actual_backend is backend and owner==backend.current() and home==paths["home"]
        calls.append("strict_msix_admission")
        return copy.deepcopy(admitted)
    monkeypatch.setattr(app,"verify_launch_bundle",verify)
    monkeypatch.setattr(app,"verify_bundle",lambda *a,**k:pytest.fail("MSIX preparation cannot enter legacy sidecar path"))
    target=paths["home"]/".tmp/bundled-marketplaces"/tools.MARKETPLACE
    assert not target.exists()
    host_before=host.read_bytes()
    result=app.prepare_runtime(descriptor,app.sha(descriptor),Path(request["baseline"]),backend)
    assert calls==["strict_msix_admission"]
    assert result["manifest"]==str(descriptor) and result["manifest_sha256"]==app.sha(descriptor)
    assert result["runtime_package"]==context and result["runtime_executed"] is False
    assert descriptor.read_bytes()==descriptor_before and host.read_bytes()==host_before
    assert not (descriptor.parent/"app-runtime-manifest.json").exists()
    inventory=tools.load_inventory()
    assert tools.verify_resources(target,inventory)["kind"]=="original"
    assert not backend.spawned


@pytest.mark.parametrize("mode", ["registered_core", "legacy"])
def test_packaged_one_shot_preserves_native_mode_conflict_failure(tools_fixture,tmp_path,monkeypatch,mode):
    # This mock proves the explicit request and error propagation, not the OS
    # alias mapping or the native initializer; those need actual qualification.
    _,home,_,_,_,_=tools_fixture
    native=tmp_path/"installed/runtime/codex.exe"
    native.parent.mkdir(parents=True);native.write_bytes(b"native mock, never executed")
    context=package_context(tmp_path,native,mode)
    admitted={"files":{"codex.exe":{"path":str(native),"sha256":app.sha(native)}},"runtime_package":context}
    monkeypatch.setattr(app,"baseline",lambda path:{})
    monkeypatch.setattr(app,"WindowsBackend",lambda:SimpleNamespace(current=lambda:{"sid":"sid"}))
    monkeypatch.setattr(app,"verify_launch_bundle",lambda *args:copy.deepcopy(admitted))
    attempts=[]
    actual_kernel_mode="0" if mode=="registered_core" else "1"
    class RejectedNative:
        pid=12345;returncode=1
        def poll(self):return self.returncode
        def communicate(self,timeout=None):return b"",b"native mode request conflicts with actual AUMID"
    def spawn(argv,**kw):
        requested=kw["env"]["CODEX_WINDOWS_REGISTERED_CORE"]
        assert requested==("1" if mode=="registered_core" else "0")
        assert requested!=actual_kernel_mode
        attempts.append((argv,requested))
        return RejectedNative()
    monkeypatch.setattr(tools.subprocess,"Popen",spawn)
    receipt={"owner_sid":"sid"}
    cli=tools.NativeCLI(native,app.sha(native),home,receipt,None,runtime_package=context)
    with pytest.raises(ValueError,match="Native plugin command failed"):
        cli.call(["plugin","remove",tools.PLUGIN_ID,"--json"])
    assert len(attempts)==1 and receipt["commands"][0]["exit_code"]==1
    assert receipt["commands"][0]["runtime_activation"]["mode"]==mode
