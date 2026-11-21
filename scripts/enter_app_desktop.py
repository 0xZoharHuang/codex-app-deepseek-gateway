"""Open the reserved O169 desktop from the qualified ordinary Windows session.

Run with the same independent Python used to install the active gateway. This
process owns only a desktop, a coordination mutex and one system PowerShell
child. It can outlive or uninstall any managed gateway release.
"""
from __future__ import annotations

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    raise SystemExit("Run this script with independent Python using -I -B.")

import argparse
import ctypes as ct
from contextlib import contextmanager
from ctypes import wintypes as wt
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

PROJECT = "codex-app-deepseek-gateway"
STATION = "WinSta0"
DEFAULT = "Default"
DESKTOP = "CADSG-P169-Acceptance-D857"
# The release records the signed Store App and its bundled runtime identity.
BASELINE = {
    "app_version": "26.1111.2228.0",
    "native_version": "0.169.0-alpha.11.1",
    "native_sha256": "094d4db493de22025712cc5963854e7654b7c97f4cee795e3e986270a0f4f6b4",
}
ENTRY = "venv/Scripts/codex-app-deepseek.exe"
READ_OBJECTS = 0x0001
CREATE_WINDOW = 0x0002
WRITE_OBJECTS = 0x0080
SWITCH_DESKTOP = 0x0100
READ_CONTROL = 0x00020000
TOKEN_QUERY = 0x0008
OWNER_SECURITY_INFORMATION = 0x0001
ERROR_FILE_NOT_FOUND = 2
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_ALREADY_EXISTS = 183
CREATE_NEW_CONSOLE = 0x0010
CREATE_UNICODE_ENVIRONMENT = 0x0400
CREATE_NO_WINDOW = 0x08000000
WAIT_TIMEOUT = 258


class Startup(ct.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("reserved", wt.LPWSTR), ("desktop", wt.LPWSTR),
        ("title", wt.LPWSTR), ("x", wt.DWORD), ("y", wt.DWORD),
        ("x_size", wt.DWORD), ("y_size", wt.DWORD), ("x_count", wt.DWORD),
        ("y_count", wt.DWORD), ("fill", wt.DWORD), ("flags", wt.DWORD),
        ("show", wt.WORD), ("reserved_bytes", wt.WORD), ("reserved_buffer", ct.c_void_p),
        ("stdin", wt.HANDLE), ("stdout", wt.HANDLE), ("stderr", wt.HANDLE),
    ]


class Process(ct.Structure):
    _fields_ = [("handle", wt.HANDLE), ("thread", wt.HANDLE),
                ("pid", wt.DWORD), ("tid", wt.DWORD)]


class DesktopError(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise DesktopError(reason)


def plain(path, *, directory=False):
    path = Path(path)
    require(path.is_absolute() and not str(path).startswith("\\\\"),
            "Use an absolute local path.")
    for part in (path, *path.parents):
        meta = part.lstat()
        require(not (getattr(meta, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
                and not stat.S_ISLNK(meta.st_mode), "Path traverses a link or redirected object.")
    test = path.is_dir if directory else path.is_file
    require(test(), "Expected an existing plain local path.")
    return path.resolve(strict=True)


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def record(path, expected=None):
    path = plain(path)
    require(path.stat().st_size <= 16 * 1024 * 1024, "Installation record is too large.")
    raw = path.read_bytes()
    if expected is not None:
        require(isinstance(expected, str) and hashlib.sha256(raw).hexdigest() == expected,
                "Managed installation record failed verification.")
    value = json.loads(raw.decode("utf-8-sig"))
    require(isinstance(value, dict), "Managed installation record is invalid.")
    return value


def verified_entrypoint(root, base_python):
    root = plain(root, directory=True)
    active = record(root / "active.json")
    rid = active.get("release_id")
    receipt_sha = active.get("receipt_sha256")
    require(active.get("project") == PROJECT and isinstance(rid, str)
            and re.fullmatch("[0-9a-f]{20}", rid) is not None and isinstance(receipt_sha, str)
            and re.fullmatch("[0-9a-f]{64}", receipt_sha) is not None, "Activate a valid managed release first.")
    release = root / "releases" / rid
    receipt = record(release / "installation.json", receipt_sha)
    manifest_sha = receipt.get("manifest_sha256", "")
    require(receipt.get("schema_version") == 1 and receipt.get("project") == PROJECT
            and receipt.get("release_id") == rid and isinstance(manifest_sha, str)
            and re.fullmatch("[0-9a-f]{64}", manifest_sha) is not None
            and manifest_sha[:20] == rid, "Managed installation identity differs.")
    python = plain(base_python)
    require(isinstance(receipt.get("bootstrap_python"), str) and plain(receipt["bootstrap_python"]) == python
            and not python.is_relative_to(root / "releases"),
            "Use the independent Python recorded by Install.")
    manifest = record(release / "release.json", manifest_sha)
    require(manifest.get("project") == PROJECT and manifest.get("app_baseline") == BASELINE,
            "This desktop launcher requires the fixed O169 release.")
    entrypoint = plain(release / ENTRY)
    proof = receipt.get("entrypoint")
    require(isinstance(proof, dict) and proof.get("path") == ENTRY
            and proof.get("interpreter_args") == ["-I"]
            and file_digest(entrypoint) == proof.get("sha256"), "Installed CLI launcher failed verification.")
    return root, entrypoint


def bind(module, name, result, *arguments):
    function = getattr(module, name)
    function.restype, function.argtypes = result, list(arguments)
    return function


def checked(value, operation):
    if not value:
        raise DesktopError(f"{operation} failed (Win32 error {ct.get_last_error()}).")
    return value


class Windows:
    def __init__(self):
        k = ct.WinDLL("kernel32", use_last_error=True)
        u = ct.WinDLL("user32", use_last_error=True)
        a = ct.WinDLL("advapi32", use_last_error=True)
        pointer = ct.c_void_p
        dword_pointer = ct.POINTER(wt.DWORD)
        self.pid = bind(k, "GetCurrentProcessId", wt.DWORD)
        self.process = bind(k, "GetCurrentProcess", wt.HANDLE)
        self.thread = bind(k, "GetCurrentThreadId", wt.DWORD)
        self.session = bind(k, "ProcessIdToSessionId", wt.BOOL, wt.DWORD, dword_pointer)
        self.system_dir = bind(k, "GetSystemDirectoryW", wt.UINT, wt.LPWSTR, wt.UINT)
        self.mutex = bind(k, "CreateMutexW", wt.HANDLE, pointer, wt.BOOL, wt.LPCWSTR)
        self.release_mutex = bind(k, "ReleaseMutex", wt.BOOL, wt.HANDLE)
        self.close_handle = bind(k, "CloseHandle", wt.BOOL, wt.HANDLE)
        self.create_process = bind(k, "CreateProcessW", wt.BOOL, wt.LPCWSTR, wt.LPWSTR,
                                   pointer, pointer, wt.BOOL, wt.DWORD, pointer, wt.LPCWSTR,
                                   ct.POINTER(Startup), ct.POINTER(Process))
        self.wait_process = bind(k, "WaitForSingleObject", wt.DWORD, wt.HANDLE, wt.DWORD)
        self.process_exit = bind(k, "GetExitCodeProcess", wt.BOOL, wt.HANDLE, dword_pointer)
        self.window_station = bind(u, "GetProcessWindowStation", wt.HANDLE)
        self.thread_desktop = bind(u, "GetThreadDesktop", wt.HANDLE, wt.DWORD)
        self.object_info = bind(u, "GetUserObjectInformationW", wt.BOOL, wt.HANDLE, wt.INT,
                                pointer, wt.DWORD, dword_pointer)
        self.open_input = bind(u, "OpenInputDesktop", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
        self.open_desktop = bind(u, "OpenDesktopW", wt.HANDLE, wt.LPCWSTR, wt.DWORD, wt.BOOL, wt.DWORD)
        self.create_desktop = bind(u, "CreateDesktopW", wt.HANDLE, wt.LPCWSTR, wt.LPCWSTR, pointer,
                                   wt.DWORD, wt.DWORD, pointer)
        self.close_desktop = bind(u, "CloseDesktop", wt.BOOL, wt.HANDLE)
        self.switch = bind(u, "SwitchDesktop", wt.BOOL, wt.HANDLE)
        self.object_security = bind(u, "GetUserObjectSecurity", wt.BOOL, wt.HANDLE, dword_pointer,
                                    pointer, wt.DWORD, dword_pointer)
        self.token = bind(a, "OpenProcessToken", wt.BOOL, wt.HANDLE, wt.DWORD, ct.POINTER(wt.HANDLE))
        self.token_info = bind(a, "GetTokenInformation", wt.BOOL, wt.HANDLE, wt.INT, pointer,
                               wt.DWORD, dword_pointer)
        self.sd_owner = bind(a, "GetSecurityDescriptorOwner", wt.BOOL, pointer,
                             ct.POINTER(pointer), ct.POINTER(wt.BOOL))
        self.same_sid = bind(a, "EqualSid", wt.BOOL, pointer, pointer)

    def name(self, handle):
        needed = wt.DWORD()
        require(bool(handle), "Windows desktop handle is unavailable.")
        present = self.object_info(handle, 2, None, 0, ct.byref(needed))
        width = ct.sizeof(ct.c_wchar)
        require(not present and ct.get_last_error() == ERROR_INSUFFICIENT_BUFFER
                and 0 < needed.value <= 1024 and needed.value % width == 0,
                "Cannot read Windows desktop name.")
        buffer = ct.create_unicode_buffer(needed.value // width)
        checked(self.object_info(handle, 2, buffer, ct.sizeof(buffer), ct.byref(needed)),
                "Read Windows desktop name")
        return buffer.value

    @contextmanager
    def actor(self):
        ident = wt.DWORD()
        checked(self.session(self.pid(), ct.byref(ident)), "Read Windows session")
        require((ident.value, self.name(self.window_station()),
                 self.name(self.thread_desktop(self.thread()))) == (1, STATION, DEFAULT),
                "Use the supported Windows session 1 on its ordinary interactive Default desktop.")
        token = wt.HANDLE()
        checked(self.token(self.process(), TOKEN_QUERY, ct.byref(token)), "Read caller token")
        try:
            size = wt.DWORD()
            elevated = wt.DWORD()
            checked(self.token_info(token, 20, ct.byref(elevated), ct.sizeof(elevated), ct.byref(size)),
                    "Read caller elevation")
            require(elevated.value == 0, "Use a non-elevated Windows console.")
            ok = self.token_info(token, 1, None, 0, ct.byref(size))
            require(not ok and ct.get_last_error() == ERROR_INSUFFICIENT_BUFFER and 0 < size.value <= 4096,
                    "Cannot size caller identity.")
            user = ct.create_string_buffer(size.value)
            checked(self.token_info(token, 1, user, ct.sizeof(user), ct.byref(size)), "Read caller identity")
            # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first field is the SID pointer.
            yield ct.c_void_p.from_buffer(user).value
        finally:
            self.close_handle(token)

    @contextmanager
    def reservation(self):
        ct.set_last_error(0)
        handle = self.mutex(None, True, r"Local\codex-app-deepseek-O169-desktop")
        checked(handle, "Reserve private desktop")
        already_exists = ct.get_last_error() == ERROR_ALREADY_EXISTS
        try:
            require(not already_exists, "Another launcher already owns this desktop reservation.")
            yield
        finally:
            if not already_exists:
                self.release_mutex(handle)
            self.close_handle(handle)

    @contextmanager
    def input(self, *, allow_switch=False):
        rights = READ_OBJECTS | (SWITCH_DESKTOP if allow_switch else 0)
        handle = checked(self.open_input(0, False, rights), "Open active Windows desktop")
        try:
            yield handle
        finally:
            self.close_desktop(handle)

    def require_owner(self, handle, caller_sid):
        requested = wt.DWORD(OWNER_SECURITY_INFORMATION)
        needed = wt.DWORD()
        ok = self.object_security(handle, ct.byref(requested), None, 0, ct.byref(needed))
        require(not ok and ct.get_last_error() == ERROR_INSUFFICIENT_BUFFER and 0 < needed.value <= 65536,
                "Cannot size reserved desktop ownership.")
        security = ct.create_string_buffer(needed.value)
        checked(self.object_security(handle, ct.byref(requested), security, ct.sizeof(security),
                                     ct.byref(needed)), "Read reserved desktop owner")
        owner = ct.c_void_p()
        defaulted = wt.BOOL()
        checked(self.sd_owner(security, ct.byref(owner), ct.byref(defaulted)), "Resolve reserved desktop owner")
        require(bool(owner.value) and self.same_sid(caller_sid, owner), "Reserved desktop belongs to another user.")

    @contextmanager
    def fresh_desktop(self, caller_sid, *, show):
        ct.set_last_error(0)
        existing = self.open_desktop(DESKTOP, 0, False, READ_OBJECTS)
        if existing:
            self.close_desktop(existing)
            raise DesktopError("Reserved desktop already exists; quit its owned App and shell first.")
        require(ct.get_last_error() == ERROR_FILE_NOT_FOUND, "Reserved desktop absence could not be verified.")
        rights = READ_OBJECTS | CREATE_WINDOW | WRITE_OBJECTS | READ_CONTROL | (SWITCH_DESKTOP if show else 0)

        def create():
            # CreateDesktop assigns its desktop to the calling thread. Let that
            # temporary thread exit before spawning or closing the owned handle.
            ct.set_last_error(0)
            handle = checked(self.create_desktop(DESKTOP, None, None, 0, rights, None),
                             "Create reserved Windows desktop")
            return handle, ct.get_last_error()

        with ThreadPoolExecutor(max_workers=1) as worker:
            handle, indication = worker.submit(create).result()
        try:
            require(indication != ERROR_ALREADY_EXISTS, "Reserved desktop appeared while creating it.")
            require(self.name(handle) == DESKTOP, "Unexpected Windows desktop.")
            self.require_owner(handle, caller_sid)
            yield handle
        finally:
            self.close_desktop(handle)

    def powershell(self):
        buffer = ct.create_unicode_buffer(32768)
        size = checked(self.system_dir(buffer, len(buffer)), "Locate system PowerShell")
        require(size < len(buffer), "System directory path is too long.")
        return plain(Path(buffer.value) / "WindowsPowerShell/v1.0/powershell.exe")

    @contextmanager
    def shell(self, argv, environment, root, *, show):
        # CPython's subprocess.STARTUPINFO does not expose lpDesktop to
        # CreateProcessW. Use the native struct instead of a fallback launch.
        startup = Startup()
        startup.cb = ct.sizeof(startup)
        startup.desktop = STATION + "\\" + DESKTOP
        process = Process()
        command = ct.create_unicode_buffer(subprocess.list2cmdline(argv))
        block = ct.create_unicode_buffer("\0".join(f"{key}={environment[key]}" for key in
                                                  sorted(environment, key=str.upper)) + "\0")
        flags = CREATE_UNICODE_ENVIRONMENT | (CREATE_NEW_CONSOLE if show else CREATE_NO_WINDOW)
        checked(self.create_process(argv[0], command, None, None, False, flags, block, str(root),
                                    ct.byref(startup), ct.byref(process)), "Start owned system PowerShell")
        try:
            checked(self.close_handle(process.thread), "Close new PowerShell thread handle")
            yield process
        finally:
            self.close_handle(process.handle)

    def wait_shell(self, process):
        result = self.wait_process(process.handle, 500)
        while result == WAIT_TIMEOUT:
            result = self.wait_process(process.handle, 500)
        require(result == 0, "Owned PowerShell exit could not be established.")
        code = wt.DWORD()
        checked(self.process_exit(process.handle, ct.byref(code)), "Read owned PowerShell exit code")
        return code.value


def hidden_script(path):
    source = plain(path)
    require(source.suffix.lower() == ".ps1", "Supply a trusted local PowerShell script.")
    return source


def enter(root, entry, *, show, script, windows, output=print):
    if show:
        require(sys.stdin.isatty() and sys.stdout.isatty(),
                "--show requires an attended Windows console on a dedicated guest.")
    args = [str(windows.powershell()), "-NoLogo", "-NoProfile"]
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {"CADSG_DESKTOP_INSTALL_ROOT", "CADSG_DESKTOP_COMMAND"}}
    env.update(CADSG_DESKTOP_INSTALL_ROOT=str(root), CADSG_DESKTOP_COMMAND=str(entry))
    if show:
        args.extend(["-NoExit", "-Command",
                     "Write-Host 'CADSG desktop. Use $env:CADSG_DESKTOP_COMMAND and "
                     "$env:CADSG_DESKTOP_INSTALL_ROOT. Quit ChatGPT before typing exit.'"])
    else:
        args.extend(["-NonInteractive", "-File", str(script)])

    with windows.actor() as caller_sid, windows.reservation(), windows.input(allow_switch=show) as previous:
        require(windows.name(previous) == DEFAULT, "Default must be the active input desktop.")
        with windows.fresh_desktop(caller_sid, show=show) as desktop:
            switched = False
            try:
                if show:
                    with windows.input() as current:
                        require(windows.name(current) == DEFAULT, "The active desktop changed before viewing.")
                    checked(windows.switch(desktop), "Show private Windows desktop")
                    switched = True
                with windows.shell(args, env, root, show=show) as child:
                    output(json.dumps({"status": "owned_desktop_child_started", "desktop": DESKTOP,
                                       "child_pid": child.pid, "switched_for_viewing": switched}), flush=True)
                    return windows.wait_shell(child)
            finally:
                if switched:
                    with windows.input() as current:
                        if windows.name(current) == DESKTOP:
                            checked(windows.switch(previous), "Return to the original desktop")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--show", action="store_true", help="Open a visible shell only on your dedicated guest.")
    mode.add_argument("--script", type=Path, help="Run a trusted local .ps1 without switching the input desktop.")
    args = parser.parse_args(argv)
    try:
        require(os.name == "nt" and ct.sizeof(ct.c_void_p) == 8 and sys.prefix == sys.base_prefix,
                "Use an independent Windows x64 Python, not the installed gateway venv.")
        root, entry = verified_entrypoint(args.install_root, Path(sys.executable))
        script = hidden_script(args.script) if args.script is not None else None
        return enter(root, entry, show=args.show, script=script, windows=Windows())
    except DesktopError as error:
        print(f"CADSG desktop: {error}", file=sys.stderr)
        return 2
    except (KeyError, OSError, ValueError):
        print("CADSG desktop: could not verify the installation or open the requested desktop session.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
