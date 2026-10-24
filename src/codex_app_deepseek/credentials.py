"""Dedicated Windows credentials; no App login or existing CDS store mutation."""
from __future__ import annotations
import ctypes
import secrets
import sys

UPSTREAM_TARGET = "ai.codex.app-deepseek.openrouter"
LOCAL_TARGET = "ai.codex.app-deepseek.local-token"
LEGACY_CDS_TARGET = "ai.codex.deepseek.openrouter"


class CredentialUnavailable(RuntimeError):
    pass


def _api():
    if sys.platform != "win32":
        raise CredentialUnavailable("This credential backend requires Windows")
    from ctypes import wintypes
    class Credential(ctypes.Structure):
        _fields_ = [("Flags",wintypes.DWORD),("Type",wintypes.DWORD),("TargetName",wintypes.LPWSTR),
                    ("Comment",wintypes.LPWSTR),("LastWritten",wintypes.FILETIME),
                    ("CredentialBlobSize",wintypes.DWORD),("CredentialBlob",ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist",wintypes.DWORD),("AttributeCount",wintypes.DWORD),
                    ("Attributes",ctypes.c_void_p),("TargetAlias",wintypes.LPWSTR),("UserName",wintypes.LPWSTR)]
    api = ctypes.WinDLL("advapi32",use_last_error=True)
    api.CredReadW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(Credential))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(Credential),wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api,Credential


def read(target: str) -> str:
    if target not in {UPSTREAM_TARGET,LOCAL_TARGET,LEGACY_CDS_TARGET}:
        raise ValueError("Unrecognized project credential target")
    api,kind = _api(); pointer = ctypes.POINTER(kind)()
    if not api.CredReadW(target,1,0,ctypes.byref(pointer)):
        code = ctypes.get_last_error()
        raise CredentialUnavailable(f"Windows credential unavailable (error {code}); use the intended interactive user session")
    try:
        value = ctypes.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode("utf-8").strip()
        if not value: raise CredentialUnavailable("Stored credential is empty")
        return value
    finally:
        api.CredFree(pointer)


def write(target: str, value: str) -> None:
    if target not in {UPSTREAM_TARGET,LOCAL_TARGET}:
        raise ValueError("Existing CDS and App credentials are read-only to this project")
    if not value or not value.strip(): raise ValueError("Empty credential")
    api,kind = _api(); raw = value.strip().encode("utf-8")
    buffer = (ctypes.c_ubyte*len(raw)).from_buffer_copy(raw)
    record = kind(Type=1,TargetName=target,CredentialBlobSize=len(raw),CredentialBlob=buffer,Persist=2,UserName="default")
    if not api.CredWriteW(ctypes.byref(record),0):
        raise CredentialUnavailable(f"Windows credential write failed (error {ctypes.get_last_error()})")


def initialize(*, import_existing_cds: bool=False) -> dict:
    result = {}
    try: read(UPSTREAM_TARGET); result["upstream"] = "already_configured"
    except CredentialUnavailable:
        if not import_existing_cds: raise
        value = read(LEGACY_CDS_TARGET)
        write(UPSTREAM_TARGET,value)
        del value
        result["upstream"] = "copied_in_memory_from_authorized_windows_cds_store"
    try: read(LOCAL_TARGET); result["gateway_auth"] = "already_configured"
    except CredentialUnavailable:
        write(LOCAL_TARGET,secrets.token_urlsafe(48)); result["gateway_auth"] = "created"
    result["secret_values_in_output"] = False
    return result
