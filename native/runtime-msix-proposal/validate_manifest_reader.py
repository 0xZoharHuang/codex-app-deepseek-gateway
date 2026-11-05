"""Parse one draft manifest with the installed Windows Appx reader. Never deploy."""
from pathlib import Path
import ctypes as c
import hashlib
import json
import sys
import uuid

class GUID(c.Structure):
    _fields_=[("Data1",c.c_uint32),("Data2",c.c_uint16),("Data3",c.c_uint16),("Data4",c.c_ubyte*8)]

def guid(value):
    return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)

def invoke(pointer,index,result,args,*values):
    table=c.cast(pointer,c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(result,c.c_void_p,*args)(table[index])(pointer,*values)

def validate(path):
    raw=Path(path).read_bytes()
    ole=c.OleDLL("ole32");shell=c.WinDLL("shlwapi")
    ole.CoInitializeEx.argtypes=[c.c_void_p,c.c_uint32];ole.CoInitializeEx.restype=c.c_long
    ole.CoCreateInstance.argtypes=[c.POINTER(GUID),c.c_void_p,c.c_uint32,c.POINTER(GUID),c.POINTER(c.c_void_p)]
    ole.CoCreateInstance.restype=c.c_long
    shell.SHCreateMemStream.argtypes=[c.c_void_p,c.c_uint32];shell.SHCreateMemStream.restype=c.c_void_p
    status=ole.CoInitializeEx(None,2)
    if status<0:raise OSError(status,"CoInitializeEx")
    factory=c.c_void_p();reader=c.c_void_p();stream=None
    try:
        clsid=guid("5842a140-ff9f-4166-8f5c-62f5b7b0c781");iid=guid("beb94909-e451-438b-b5a7-d79e767b75d8")
        status=ole.CoCreateInstance(c.byref(clsid),None,1,c.byref(iid),c.byref(factory))
        if status<0:raise OSError(status,"CoCreateInstance(IAppxFactory)")
        storage=c.create_string_buffer(raw);stream=shell.SHCreateMemStream(storage,len(raw))
        if not stream:raise OSError("SHCreateMemStream")
        status=invoke(factory,5,c.c_long,[c.c_void_p,c.POINTER(c.c_void_p)],stream,c.byref(reader))
        return {"method":"Installed Windows IAppxFactory::CreateManifestReader over memory IStream",
                "manifest_sha256":hashlib.sha256(raw).hexdigest(),"hresult":f"0x{status&0xffffffff:08x}",
                "manifest_reader_created":bool(reader.value),"manifest_schema_reader_accepted":status>=0,
                "native_assets_built_or_present":False,"package_signed":False,"package_installed":False,
                "limits":"Does not qualify executable closure, signer trust, service deployment or actual alias/kernel identity/stdio."}
    finally:
        if reader.value:invoke(reader,2,c.c_uint32,[])
        if stream:invoke(stream,2,c.c_uint32,[])
        if factory.value:invoke(factory,2,c.c_uint32,[])
        ole.CoUninitialize()

if __name__=="__main__":
    result=validate(sys.argv[1]);print(json.dumps(result,indent=2))
    raise SystemExit(0 if result["manifest_schema_reader_accepted"] else 1)
