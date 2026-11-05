"""MSIX descriptor bindings only: OS evidence is mocked; no package is deployed or executed."""
import copy
import json
from pathlib import Path
import pytest
from codex_app_deepseek import runtime_package as runtime
from codex_app_deepseek import isolated_app as app
from test_isolated_app import fixture

@pytest.fixture
def evidence(tmp_path):
    package_root=tmp_path/"WindowsApps"/"CADSG.NativeRuntime_0.0.0.1_x64__123456789abcd"
    (package_root/"runtime").mkdir(parents=True)
    family="CADSG.NativeRuntime_123456789abcd"
    alias_dir=tmp_path/"current-user"/"Microsoft/WindowsApps"/family
    alias_dir.mkdir(parents=True)
    alias=alias_dir/"cadsg-native.exe";alias.write_bytes(b"unit alias locator: never executed")
    rows=[]
    for name in sorted(runtime.BINARIES):
        path=package_root/"runtime"/name;path.write_bytes(("unit native bytes: never executed:"+name).encode())
        rows.append({"name":name,"size":path.stat().st_size,"sha256":runtime.checksum(path),"path":str(path)})
    home=tmp_path/"dedicated-home";home.mkdir()
    package={"name":runtime.PACKAGE_NAME,"full_name":package_root.name,"family":family,
             "version":"0.0.0.1","publisher":"CN=Unit Fixture","signature_kind":"Enterprise",
             "signer_thumbprint":"A"*40,"manifest_sha256":"b"*64,"signature_sha256":"c"*64}
    descriptor={"schema_version":1,"kind":runtime.KIND,"status":"installed-not-app-qualified",
                "mode":"registered_core","native_version":"0.165.0-alpha.1.2","upstream_commit":"720fec",
                "source_sha256":"d"*64,"build_lock_sha256":"e"*64,"app_baseline_sha256":app.BASELINE_SHA256,
                "owner_sid":"own-fixture-sid","home":str(home),"package":package,"outputs":rows}
    observed={"package":{**{k:v for k,v in package.items() if k not in {"signer_thumbprint","manifest_sha256","signature_sha256"}},
                          "status":"Ok","development":False,"location":str(package_root)},
              "package_signature_status":"Valid","signer_thumbprint":"A"*40,
              "files":{**{"runtime/"+row["name"]:row["sha256"] for row in rows},
                       "AppxManifest.xml":"b"*64,"AppxSignature.p7x":"c"*64},
              "activation":{"alias":alias.name,"application_id":"RuntimeRegisteredCore",
                            "executable":r"runtime\codex.exe","path":str(alias),
                            "known_alias_directory":str(alias_dir),"reparse_tag":runtime.APPEXECLINK}}
    class Backend:
        def runtime_package_document(self,document):
            assert document==descriptor
            return observed
    path=tmp_path/"runtime-descriptor.json"
    def write():
        path.write_text(json.dumps(descriptor),encoding="utf-8")
        return runtime.checksum(path)
    def verify():
        return runtime.verify(path,write(),
            {"native_version":"0.165.0-alpha.1.2","native_upstream_commit":"720fec"},
            {"sid":"own-fixture-sid"},home,Backend())
    return descriptor,observed,path,home,verify

@pytest.mark.parametrize("mode",["registered_core","legacy"])
def test_explicit_package_alias_mode_keeps_physical_native_binding(evidence,mode):
    descriptor,observed,path,home,verify=evidence
    descriptor["mode"]=mode
    app_id,alias=runtime.MODES[mode]
    target=Path(observed["activation"]["known_alias_directory"])/alias
    target.write_bytes(b"unit alias locator: never executed")
    observed["activation"].update(alias=alias,application_id=app_id,path=str(target))
    result=verify()
    assert set(result["files"])==runtime.BINARIES
    assert result["runtime_package"]["alias_path"]==str(target)
    assert Path(result["files"]["codex.exe"]["path"])!=target
    runtime.validate_control_binding(result["runtime_package"],result["files"]["codex.exe"]["path"],
                                    {"CODEX_CLI_PATH":str(target)})
    activation={"family":descriptor["package"]["family"],
                "full_name":descriptor["package"]["full_name"],
                "application_id":descriptor["package"]["family"]+"!"+app_id}
    runtime.verify_activation(result["runtime_package"],activation)
    activation["application_id"]=descriptor["package"]["family"]+"!"+(
        "CodeModeHostRegistered" if mode=="registered_core" else "CodeModeHostLegacy")
    runtime.verify_activation(result["runtime_package"],activation,role="host")
    with pytest.raises(ValueError):runtime.verify_activation(result["runtime_package"],activation)

@pytest.mark.parametrize("change",["owner","home","unknown-mode","missing-service","extra-output",
    "foreign-family","development","bad-signature","bad-status","alias-role","alias-tag","alias-directory","bytes"])
def test_package_alias_cannot_adopt_foreign_or_incomplete_evidence(evidence,change,tmp_path):
    descriptor,observed,path,home,verify=evidence
    if change=="owner":descriptor["owner_sid"]="another-owner"
    elif change=="home":descriptor["home"]=str(tmp_path/"other-home")
    elif change=="unknown-mode":descriptor["mode"]="automatic"
    elif change=="missing-service":descriptor["outputs"]=[x for x in descriptor["outputs"] if x["name"]!="codex-windows-sandbox-service.exe"]
    elif change=="extra-output":descriptor["outputs"].append(copy.deepcopy(descriptor["outputs"][0]))
    elif change=="foreign-family":observed["package"]["family"]="OpenAI.Codex_otherpublisher"
    elif change=="development":observed["package"]["development"]=True
    elif change=="bad-signature":observed["package_signature_status"]="NotTrusted"
    elif change=="bad-status":observed["package"]["status"]="Tampered"
    elif change=="alias-role":observed["activation"]["application_id"]="RuntimeLegacy"
    elif change=="alias-tag":observed["activation"]["reparse_tag"]=0xA000000C
    elif change=="alias-directory":observed["activation"]["path"]=str(tmp_path/"cadsg-native.exe")
    else:Path(descriptor["outputs"][0]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError):verify()

@pytest.mark.parametrize("bad",["unknown","conflicting-mode","foreign-family","foreign-version"])
def test_host_and_cli_require_matching_kernel_activation(evidence,bad):
    descriptor,observed,path,home,verify=evidence
    runtime_info=verify()["runtime_package"]
    value={"family":descriptor["package"]["family"],"full_name":descriptor["package"]["full_name"],
           "application_id":descriptor["package"]["family"]+"!RuntimeRegisteredCore"}
    if bad=="unknown":value["application_id"]=descriptor["package"]["family"]+"!Unknown"
    elif bad=="conflicting-mode":value["application_id"]=descriptor["package"]["family"]+"!CodeModeHostLegacy"
    elif bad=="foreign-family":value["family"]="OpenAI.Codex_otherpublisher"
    else:value["full_name"]="CADSG.NativeRuntime_other_version"
    with pytest.raises(ValueError):runtime.verify_activation(runtime_info,value,role="host")

def test_descriptor_rejects_duplicate_keys_and_stale_hash(evidence):
    descriptor,observed,path,home,verify=evidence
    verify()
    raw=path.read_text()
    path.write_text(raw[:-1]+',"mode":"legacy"}')
    with pytest.raises(ValueError,match="duplicate"):runtime.load(path,runtime.checksum(path))
    with pytest.raises(ValueError,match="checksum"):runtime.load(path,"0"*64)

def test_control_selector_is_exact_qualified_alias_not_native_or_arbitrary_command(evidence):
    descriptor,observed,path,home,verify=evidence
    result=verify();info=result["runtime_package"];native=result["files"]["codex.exe"]["path"]
    for wrong in [native,"cadsg-native.exe",info["alias_path"]+" --unsafe"]:
        with pytest.raises(ValueError):
            runtime.validate_control_binding(info,native,{"CODEX_CLI_PATH":wrong})


@pytest.mark.parametrize("mode",["registered_core","legacy"])
def test_launch_preflight_selects_qualified_alias_in_separate_branch(fixture,evidence,mode):
    paths,expected,old_path,old_manifest,request,backend=fixture
    descriptor,observed,path,home,verify=evidence
    descriptor.update(mode=mode,owner_sid=backend.current()["sid"],home=str(paths["home"]),
                      native_version=expected["native_version"],upstream_commit=expected["native_upstream_commit"],
                      app_baseline_sha256=app.BASELINE_SHA256)
    app_id,alias=runtime.MODES[mode]
    alias_path=Path(observed["activation"]["known_alias_directory"])/alias
    alias_path.write_bytes(b"unit alias locator: never executed")
    observed["activation"].update(alias=alias,application_id=app_id,path=str(alias_path))
    backend.runtime_package_document=lambda value:observed
    path.write_text(json.dumps(descriptor))
    request.update(native_manifest=str(path),native_manifest_sha256=runtime.checksum(path))
    checked=app.validate_request(request,backend)
    assert checked["environment"]["CODEX_CLI_PATH"]==str(alias_path)
    assert checked["bundle"]["files"]["codex.exe"]["path"]!=str(alias_path)
    assert checked["bundle"]["runtime_package"]["mode"]==mode
    assert not backend.spawned and not paths["launches"].exists()

def test_launch_observation_requires_new_kernel_activation_without_relaxing_image(fixture,evidence):
    paths,expected,old_path,old_manifest,request,backend=fixture
    descriptor,observed,path,home,verify=evidence
    descriptor.update(owner_sid=backend.current()["sid"],home=str(paths["home"]),
                      native_version=expected["native_version"],upstream_commit=expected["native_upstream_commit"],
                      app_baseline_sha256=app.BASELINE_SHA256)
    backend.runtime_package_document=lambda value:observed
    path.write_text(json.dumps(descriptor))
    request.update(native_manifest=str(path),native_manifest_sha256=runtime.checksum(path))
    native=next(row for row in descriptor["outputs"] if row["name"]=="codex.exe")
    activation={"family":descriptor["package"]["family"],"full_name":descriptor["package"]["full_name"],
                "application_id":descriptor["package"]["family"]+"!RuntimeRegisteredCore"}
    backend.runtime_process_activation=lambda identity:dict(activation)
    calls=[]
    def control(root,*,executable,expected_sha256,environment,auxiliary_exits=None,runtime_package=None):
        assert executable==native["path"] and expected_sha256==native["sha256"]
        assert runtime_package["application_id"]=="RuntimeRegisteredCore"
        runtime.validate_control_binding(runtime_package,executable,environment)
        calls.append(runtime_package)
        return [root,{**root,"pid":102,"exe":native["path"]}]
    backend.native_control_plane=control
    prepared=app.prepare_launch(request,backend)
    result=app.inside_launch(prepared["plan"],prepared["plan_sha256"],backend)
    assert result["status"]=="started_expected_backend" and result["app_runtime_acceptance"] is False
    assert calls and result["native_processes"][0]["runtime_activation"]==activation


def test_protected_core_owner_blocks_only_legacy_same_home(evidence,tmp_path):
    descriptor,observed,path,home,verify=evidence
    owner={"sid":"own-fixture-sid"}
    snapshot={"state_absent":False,"owner_sid":owner["sid"],"home":str(home),"runtime_present":True}
    with pytest.raises(ValueError,match="teardown"):
        runtime.require_mode_available(None,snapshot,owner,home)
    with pytest.raises(ValueError,match="teardown"):
        runtime.require_mode_available({"mode":"legacy"},snapshot,owner,home)
    runtime.require_mode_available({"mode":"registered_core"},snapshot,owner,home)
    runtime.require_mode_available(None,snapshot,owner,tmp_path/"another-home")
    with pytest.raises(ValueError,match="another owner"):
        runtime.require_mode_available({"mode":"registered_core"},snapshot,owner,tmp_path/"another-home")
    runtime.require_mode_available(None,{"state_absent":True},owner,home)
