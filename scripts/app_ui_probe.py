"""Observe only a newly owned Codex App window in the existing admin session.

No desktop switching, global input, login, prompt submission, or approval action.
Run through windows_interactive.ps1 with output_policy=discard.
"""
from __future__ import annotations
import argparse,ctypes,hashlib,json,os,pathlib,struct,subprocess,time,zlib
from ctypes import wintypes
import psutil,win32gui,win32ui
from pywinauto import Desktop

ROOT=pathlib.Path(__file__).resolve().parents[1]
APP=pathlib.Path(r"C:\Program Files\WindowsApps\OpenAI.Codex_26.1016.8120.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe")
KERNEL=ctypes.WinDLL("kernel32",use_last_error=True)
USER=ctypes.WinDLL("user32",use_last_error=True)
GDI=ctypes.WinDLL("gdi32",use_last_error=True)
KERNEL.ProcessIdToSessionId.argtypes=[wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
KERNEL.ProcessIdToSessionId.restype=wintypes.BOOL
USER.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
USER.GetWindowThreadProcessId.restype=wintypes.DWORD
USER.PrintWindow.argtypes=[wintypes.HWND,wintypes.HDC,wintypes.UINT]
USER.PrintWindow.restype=wintypes.BOOL
GDI.GetDIBits.argtypes=[wintypes.HDC,wintypes.HBITMAP,wintypes.UINT,wintypes.UINT,ctypes.c_void_p,ctypes.c_void_p,wintypes.UINT]
GDI.GetDIBits.restype=ctypes.c_int

def session(pid):
    value=wintypes.DWORD()
    if not KERNEL.ProcessIdToSessionId(pid,ctypes.byref(value)):raise ctypes.WinError(ctypes.get_last_error())
    return value.value

def save(path,value):
    temp=path.with_name(path.name+f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value,indent=2,ensure_ascii=True),encoding="utf-8")
    os.replace(temp,path)

def identity(process):
    return {"pid":process.pid,"created":process.create_time(),"exe":process.exe(),
            "session":session(process.pid),"ppid":process.ppid(),"name":process.name()}

def live(item):
    try:return psutil.Process(item["pid"]).create_time()==item["created"]
    except (psutil.NoSuchProcess,psutil.AccessDenied):return False

def discover(owner):
    known={item["pid"]:item for item in owner["owned_processes"]}
    for _ in range(4):
        changed=False
        for process in psutil.process_iter(["pid","ppid","create_time"]):
            item=process.info
            if item["pid"] in known or item["ppid"] not in known or item["create_time"]<owner["launched_at"]-1:continue
            if not live(known[item["ppid"]]):continue
            try:
                if session(item["pid"])!=owner["session"]:continue
                detail=identity(process)
            except (psutil.Error,OSError):continue
            known[item["pid"]]=detail;changed=True
        if not changed:break
    owner["owned_processes"]=list(known.values())
    return [item for item in known.values() if live(item)]

def windows(owned):
    pids={item["pid"] for item in owned}
    result=[]
    def callback(hwnd,_):
        pid=wintypes.DWORD();USER.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value in pids and win32gui.IsWindowVisible(hwnd):
            rect=win32gui.GetWindowRect(hwnd)
            if rect[2]>rect[0] and rect[3]>rect[1]:
                result.append({"hwnd":hwnd,"pid":pid.value,"title":win32gui.GetWindowText(hwnd),
                               "class_name":win32gui.GetClassName(hwnd),"rect":list(rect)})
        return True
    win32gui.EnumWindows(callback,None)
    return result

def activate_official_app():
    from comtypes import GUID,IUnknown,COMMETHOD,HRESULT,CLSCTX_LOCAL_SERVER
    from comtypes.client import CreateObject
    class IApplicationActivationManager(IUnknown):
        _iid_=GUID("{2e941141-7f97-4756-ba1d-9decde894a3d}")
        _methods_=[COMMETHOD([],HRESULT,"ActivateApplication",
                            (["in"],wintypes.LPCWSTR,"appUserModelId"),
                            (["in"],wintypes.LPCWSTR,"arguments"),
                            (["in"],wintypes.DWORD,"options"),
                            (["out"],ctypes.POINTER(wintypes.DWORD),"processId"))]
    manager=CreateObject(GUID("{45BA127D-10A8-46EA-8AB7-56EA9078943C}"),
                         interface=IApplicationActivationManager,
                         clsctx=CLSCTX_LOCAL_SERVER)
    pid=manager.ActivateApplication("OpenAI.Codex_2p2nqsd0c76g0!App",
                                    "--force-renderer-accessibility",0)
    if not isinstance(pid,int) or pid<=0:
        raise RuntimeError("Packaged activation did not return a process id")
    return pid

def png_chunk(kind,data):
    return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data)&0xffffffff)

def screenshot(window,path):
    hwnd=window["hwnd"];left,top,right,bottom=win32gui.GetWindowRect(hwnd)
    width=right-left;height=bottom-top
    if not 1<=width<=8192 or not 1<=height<=8192:raise ValueError("Invalid owned window rectangle")
    dc=win32gui.GetWindowDC(hwnd);source=win32ui.CreateDCFromHandle(dc)
    memory=source.CreateCompatibleDC();bitmap=win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source,width,height);old=memory.SelectObject(bitmap)
    try:
        printed=bool(USER.PrintWindow(hwnd,memory.GetSafeHdc(),2))
        memory.SelectObject(old)
        header=struct.pack("<IiiHHIIiiII",40,width,-height,1,32,0,width*height*4,0,0,0,0)
        info=ctypes.create_string_buffer(header+b"\0"*16)
        pixels=ctypes.create_string_buffer(width*height*4)
        lines=GDI.GetDIBits(source.GetSafeHdc(),bitmap.GetHandle(),0,height,pixels,info,0)
        if lines!=height:raise OSError("GetDIBits did not return all owned-window rows")
        raw=pixels.raw;rows=[]
        for y in range(height):
            bgra=raw[y*width*4:(y+1)*width*4];rgba=bytearray(len(bgra))
            rgba[0::4]=bgra[2::4];rgba[1::4]=bgra[1::4];rgba[2::4]=bgra[0::4];rgba[3::4]=b"\xff"*width
            rows.append(b"\0"+rgba)
        png=b"\x89PNG\r\n\x1a\n"+png_chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,6,0,0,0))+png_chunk(b"IDAT",zlib.compress(b"".join(rows)))+png_chunk(b"IEND",b"")
        path.write_bytes(png)
        step=max(4,(len(raw)//4096)//4*4)
        colors=len({raw[i:i+3] for i in range(0,len(raw)-3,step)})
        return {"path":str(path),"bytes":len(png),"sha256":hashlib.sha256(png).hexdigest(),
                "printwindow_return":printed,"sampled_colors":colors,"width":width,"height":height}
    finally:
        win32gui.DeleteObject(bitmap.GetHandle());memory.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)

def inspect(owner,directory,label):
    owned=discover(owner);save(directory/"owner.json",owner)
    found=windows(owned);observations=[]
    for window in found:
        item=dict(window)
        try:
            control=Desktop(backend="uia").window(handle=window["hwnd"]).wrapper_object()
            tree=[]
            for child in [control,*control.descendants()][:2000]:
                entry=child.element_info
                rect=entry.rectangle
                detail={"name":entry.name,"automation_id":entry.automation_id,
                        "control_type":entry.control_type,"class_name":entry.class_name,
                        "pid":entry.process_id,"enabled":child.is_enabled(),"rect":[rect.left,rect.top,rect.right,rect.bottom]}
                if entry.control_type in ("Button","MenuItem","Edit","ComboBox","TreeItem","ListItem"):
                    detail["patterns"]={name:bool(entry.element.GetCurrentPropertyValue(identifier))
                                        for name,identifier in (("invoke",30031),("expand",30028),
                                                                ("select",30036),("value",30043),
                                                                ("legacy",30090))}
                if entry.control_type=="Edit" and entry.name in ("Project name","Do anything") and detail.get("patterns",{}).get("value"):
                    detail["value"]=child.iface_value.CurrentValue
                tree.append(detail)
            target=directory/f"{label}-{window['hwnd']}-uia.json";save(target,tree)
            item["uia_path"]=str(target);item["uia_elements"]=len(tree)
        except Exception as exc:item["uia_error"]=type(exc).__name__+": "+str(exc)
        try:item["screenshot"]=screenshot(window,directory/f"{label}-{window['hwnd']}.png")
        except Exception as exc:item["screenshot_error"]=type(exc).__name__+": "+str(exc)
        observations.append(item)
    result={"at":time.time(),"session":session(os.getpid()),"run_id":owner["run_id"],
            "owned_processes":owned,"windows":observations,"inspection_only":True,
            "global_input_used":False}
    save(directory/f"{label}.json",result)
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=["launch","inspect","skip-onboarding","action"],required=True)
    parser.add_argument("--run-id",required=True)
    parser.add_argument("--expected-session",type=int,default=1)
    parser.add_argument("--expected-heading")
    parser.add_argument("--action-json")
    args=parser.parse_args()
    if len(args.run_id)!=32 or any(x not in "0123456789abcdef" for x in args.run_id):raise ValueError("Invalid run id")
    if session(os.getpid())!=args.expected_session or args.expected_session!=1:raise RuntimeError("Refuse non-admin-session UI access")
    directory=ROOT/(".evidence/app-ui-"+args.run_id)
    if args.mode in ("inspect","skip-onboarding","action"):
        owner=json.loads((directory/"owner.json").read_text())
        if owner["session"]!=args.expected_session:raise RuntimeError("Owner session mismatch")
        label=str(int(time.time()))
        result=inspect(owner,directory,"inspect-"+label)
        if args.mode=="action":
            action_path=pathlib.Path(args.action_json).resolve()
            if not action_path.is_relative_to(directory.resolve()):
                raise ValueError("Action must be in this owned run directory")
            action=json.loads(action_path.read_text(encoding="utf-8"))
            action_id=action["id"]
            if len(action_id)!=32 or any(x not in "0123456789abcdef" for x in action_id):
                raise ValueError("Invalid action identity")
            receipt=directory/("action-"+action_id+"-receipt.json")
            if receipt.exists():
                raise RuntimeError("Action already admitted; inspect instead of replaying")
            matches=[]
            for window in result["windows"]:
                if action.get("window_title") and window["title"]!=action["window_title"]:
                    continue
                control=Desktop(backend="uia").window(handle=window["hwnd"]).wrapper_object()
                for child in [control,*control.descendants()]:
                    entry=child.element_info
                    if all(([entry.rectangle.left,entry.rectangle.top,entry.rectangle.right,entry.rectangle.bottom] if key=="rect" else getattr(entry,key))==value for key,value in action["selector"].items()):
                        matches.append((window,child))
            if len(matches)!=1:
                raise RuntimeError("Expected exactly one owned UIA target, got "+str(len(matches)))
            window,target=matches[0]
            owner_pid=wintypes.DWORD()
            USER.GetWindowThreadProcessId(window["hwnd"],ctypes.byref(owner_pid))
            if owner_pid.value!=window["pid"]:
                raise RuntimeError("Owned HWND changed before action")
            record={"id":action_id,"at":time.time(),"state":"admitted","operation":action["operation"],
                    "selector":action["selector"],"window":window,"replay_allowed":False,
                    "global_input_used":False}
            save(receipt,record)
            if action["operation"]=="invoke":
                target.iface_invoke.Invoke()
            elif action["operation"]=="legacy_default_action":
                # Explicit observed MSAA action; do not fall back after an unknown Invoke.
                from pywinauto.uia_defines import get_elem_interface
                legacy=get_elem_interface(target.element_info.element, "LegacyIAccessible")
                label=legacy.CurrentDefaultAction
                if not isinstance(label,str) or not label:
                    raise RuntimeError("Observed control has no legacy default action")
                record["legacy_default_action"]=label;save(receipt,record)
                legacy.DoDefaultAction()
            elif action["operation"]=="set_value":
                target.iface_value.SetValue(action["value"])
                deadline=time.monotonic()+5
                expected=action["value"].replace(chr(13)+chr(10),chr(10))
                paragraph=target.element_info.name in {"Do anything","Work with Codex","Work with ChatGPT"}
                def matches_value(value):
                    normalized=value.replace(chr(13)+chr(10),chr(10))
                    return normalized==expected or (paragraph and normalized==expected+chr(10))
                readback=target.iface_value.CurrentValue
                while not matches_value(readback) and time.monotonic()<deadline:
                    time.sleep(.1)
                    readback=target.iface_value.CurrentValue
                record["readback"]=readback
                record["readback_matches"]=matches_value(readback)
                record["uia_terminal_paragraph_newline"]=(paragraph and readback==expected+chr(10))
                record["state"]="value_set_readback"
                save(receipt,record)
                if not record["readback_matches"]:
                    raise RuntimeError("UIA value readback mismatch")
            elif action["operation"]=="click_window_message":
                rect=target.element_info.rectangle
                if not target.is_enabled():
                    raise RuntimeError("Observed control is disabled")
                point=((rect.left+rect.right)//2,(rect.top+rect.bottom)//2)
                x,y=win32gui.ScreenToClient(window["hwnd"],point)
                left,top,right,bottom=win32gui.GetClientRect(window["hwnd"])
                if not (left<=x<right and top<=y<bottom):
                    raise RuntimeError("Control center is outside owned client area")
                record["scoped_mouse_client_point"]=[x,y]
                save(receipt,record)
                position=(y<<16)|(x&0xffff)
                win32gui.PostMessage(window["hwnd"],0x0200,0,position)
                win32gui.PostMessage(window["hwnd"],0x0201,1,position)
                time.sleep(.1)
                win32gui.PostMessage(window["hwnd"],0x0202,0,position)
            elif action["operation"]=="press_space":
                target.element_info.element.SetFocus()
                if not target.element_info.element.GetCurrentPropertyValue(30008):
                    raise RuntimeError("Scoped control did not acquire keyboard focus")
                record["keyboard_focus_verified"]=True
                record["scoped_key"]="SPACE"
                save(receipt,record)
                win32gui.PostMessage(window["hwnd"],0x0100,0x20,0x00390001)
                time.sleep(.1)
                win32gui.PostMessage(window["hwnd"],0x0101,0x20,0xC0390001)
            elif action["operation"]=="select":
                target.iface_selection_item.Select()
            elif action["operation"]=="expand":
                target.iface_expand_collapse.Expand()
            elif action["operation"]=="collapse":
                target.iface_expand_collapse.Collapse()
            else:
                raise ValueError("Unsupported scoped UIA operation")
            record["state"]="invoked";record["finished_at"]=time.time();save(receipt,record)
            time.sleep(3)
            inspect(owner,directory,"after-action-"+action_id)
        if args.mode=="skip-onboarding":
            if not args.expected_heading:
                raise ValueError("An observed exact onboarding heading is required")
            matches=[]
            for window in result["windows"]:
                if "uia_path" not in window:
                    continue
                tree=json.loads(pathlib.Path(window["uia_path"]).read_text())
                if args.expected_heading not in [item["name"] for item in tree]:
                    continue
                control=Desktop(backend="uia").window(handle=window["hwnd"]).wrapper_object()
                for child in control.descendants():
                    if child.element_info.name=="Skip" and child.element_info.control_type=="Button":
                        matches.append((window,child))
            if len(matches)!=1:
                raise RuntimeError("Expected one observed Skip button in the owned onboarding window")
            window,button=matches[0]
            owner_pid=wintypes.DWORD()
            USER.GetWindowThreadProcessId(window["hwnd"],ctypes.byref(owner_pid))
            if owner_pid.value!=window["pid"]:
                raise RuntimeError("Owned HWND changed before invocation")
            button.iface_invoke.Invoke()
            save(directory/("skip-"+label+".json"),
                 {"at":time.time(),"action":"UIA Invoke Skip","heading":args.expected_heading,
                  "hwnd":window["hwnd"],"pid":window["pid"],"global_input":False})
            time.sleep(4)
            inspect(owner,directory,"after-skip-"+label)
        print(json.dumps({"windows":len(result["windows"]),"directory":str(directory)}),flush=True)
        return
    if directory.exists():raise RuntimeError("Do not replay a launch run")
    directory.mkdir()
    for process in psutil.process_iter(["pid","name"]):
        if (process.info.get("name") or "").lower()=="chatgpt.exe":
            try:
                if session(process.pid)==args.expected_session and pathlib.Path(process.exe())==APP:
                    raise RuntimeError("An App instance already exists in this session; no relaunch")
            except (psutil.NoSuchProcess,psutil.AccessDenied):pass
    launched_at=time.time()
    app_pid=activate_official_app()
    app_identity=identity(psutil.Process(app_pid))
    if app_identity["session"]!=args.expected_session:
        raise RuntimeError("Packaged activation returned a different session")
    if pathlib.Path(app_identity["exe"])!=APP:
        raise RuntimeError("Packaged activation returned an unexpected executable")
    owner={"run_id":args.run_id,"session":args.expected_session,"launched_at":launched_at,
           "launcher":app_identity,"owned_processes":[app_identity],
           "activation":"IApplicationActivationManager.ActivateApplication",
           "aumid":"OpenAI.Codex_2p2nqsd0c76g0!App",
           "launch_args":["--force-renderer-accessibility"],"app_executable":str(APP),"observer_pid":os.getpid()}
    save(directory/"owner.json",owner)
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        active=discover(owner);save(directory/"owner.json",owner)
        if windows(active):break
        time.sleep(.25)
    time.sleep(5)
    first=inspect(owner,directory,"initial")
    print(json.dumps({"directory":str(directory),"windows":len(first["windows"]),"persistent_owner":True}),flush=True)
    time.sleep(12)
    inspect(owner,directory,"settled")
    # Keep ownership alive without injecting input or terminating the App.
    while any(live(item) for item in discover(owner)):
        save(directory/"owner.json",owner);time.sleep(5)
    save(directory/"owner-exited.json",{"at":time.time(),"app_processes_exited":True})

if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        import sys,traceback
        try:
            run_id=sys.argv[sys.argv.index("--run-id")+1]
            if len(run_id)==32 and all(x in "0123456789abcdef" for x in run_id):
                directory=ROOT/(".evidence/app-ui-"+run_id)
                directory.mkdir(exist_ok=True)
                save(directory/"probe-error.json",{"type":type(exc).__name__,"message":str(exc),"traceback":traceback.format_exc()})
        finally:
            raise

