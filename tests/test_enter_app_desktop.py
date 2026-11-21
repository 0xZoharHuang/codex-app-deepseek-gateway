import hashlib
import importlib.util
import json
import os
from contextlib import contextmanager
import ctypes
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "enter_app_desktop", Path(__file__).resolve().parents[1] / "scripts/enter_app_desktop.py"
)
desktop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(desktop)


def save(path, value):
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def ready(tmp_path, request):
    python = tmp_path / "python.exe"
    python.write_bytes(b"independent")
    root = tmp_path / "managed"
    launch_baseline = json.loads(
        (Path(__file__).resolve().parents[1] / "src/codex_app_deepseek/assets/app-launch-baseline.json")
        .read_text(encoding="utf-8")
    )
    baseline = {"project": desktop.PROJECT, "app_baseline": {
        "app_version": launch_baseline["version"],
        "native_version": launch_baseline["native_version"],
        "native_sha256": launch_baseline["files"]["app/resources/codex.exe"],
    }}
    if getattr(request, "param", False):
        baseline["app_baseline"]["native_sha256"] = launch_baseline["candidate_native_binary_sha256"]
    digest = hashlib.sha256(json.dumps(baseline).encode()).hexdigest()
    directory = root / "releases" / digest[:20]
    entry = directory / desktop.ENTRY
    entry.parent.mkdir(parents=True)
    entry.write_bytes(b"console-launcher")
    save(directory / "release.json", baseline)
    receipt = {
        "schema_version": 1,
        "project": desktop.PROJECT,
        "release_id": directory.name,
        "manifest_sha256": digest,
        "bootstrap_python": str(python),
        "entrypoint": {"path": desktop.ENTRY, "interpreter_args": ["-I"],
                       "sha256": desktop.file_digest(entry)},
    }
    active_sha = save(directory / "installation.json", receipt)
    save(root / "active.json", {"project": desktop.PROJECT, "release_id": directory.name,
                                "receipt_sha256": active_sha})
    return python, root, entry


def test_only_the_reviewed_active_installation_enters(ready):
    python, root, entry = ready
    assert desktop.verified_entrypoint(root, python) == (root, entry)


@pytest.mark.parametrize("ready", [True], ids=["candidate-as-stock"], indirect=True)
def test_candidate_runtime_digest_cannot_replace_signed_app_digest(ready):
    python, root, _ = ready
    with pytest.raises(desktop.DesktopError, match="fixed O169 release"):
        desktop.verified_entrypoint(root, python)


@pytest.mark.parametrize("changed", ["active", "receipt", "manifest", "launcher"])
def test_changed_identity_fails_closed(ready, changed):
    python, root, entry = ready
    if changed == "active":
        active = json.loads((root / "active.json").read_bytes())
        save(root / "active.json", {**active, "receipt_sha256": None})
    elif changed == "receipt":
        entry.parents[2].joinpath("installation.json").write_bytes(b"{}")
    elif changed == "manifest":
        entry.parents[2].joinpath("release.json").write_bytes(b"{}")
    else:
        entry.write_bytes(b"unexpected bytes")
    with pytest.raises(desktop.DesktopError):
        desktop.verified_entrypoint(root, python)


class WindowState:
    def __init__(self):
        self.visible = desktop.DEFAULT
        self.switches = []
        self.events = []
        self.calls = []
        self.after_wait = lambda: None

    def powershell(self):
        return Path("powershell.exe")

    @contextmanager
    def actor(self):
        yield "owner"

    @contextmanager
    def reservation(self):
        yield

    @contextmanager
    def input(self, *, allow_switch=False):
        yield self.visible

    def name(self, handle):
        return handle

    @contextmanager
    def fresh_desktop(self, caller_sid, *, show):
        assert caller_sid == "owner"
        yield desktop.DESKTOP
        self.events.append("closed_owned_handle")

    def switch(self, target):
        self.switches.append(target)
        self.visible = target
        return True

    @contextmanager
    def shell(self, argv, environment, root, *, show):
        self.calls.append((argv, environment, root, show))
        yield SimpleNamespace(pid=123)

    def wait_shell(self, process):
        assert process.pid == 123
        self.after_wait()
        return 0


@pytest.fixture
def child_api(monkeypatch):
    monkeypatch.setattr(desktop, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True), stdout=SimpleNamespace(isatty=lambda: True),
    ))


def test_headless_script_stays_on_default(child_api, tmp_path):
    windows = WindowState()
    script = tmp_path / "acceptance.ps1"
    assert desktop.enter(tmp_path, tmp_path / "cadsg.exe", script=script, show=False,
                         windows=windows, output=lambda *a, **kw: None) == 0
    (argv, environment, workdir, show), = windows.calls
    assert argv[-2:] == ["-File", str(script)]
    assert "-ExecutionPolicy" not in argv
    assert environment["CADSG_DESKTOP_INSTALL_ROOT"] == str(tmp_path)
    assert workdir == tmp_path and not show
    assert not windows.switches
    assert windows.events == ["closed_owned_handle"]


def test_show_requires_an_attended_console(child_api, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    windows = WindowState()
    with pytest.raises(desktop.DesktopError, match="attended"):
        desktop.enter(tmp_path, tmp_path / "cadsg.exe", show=True, script=None, windows=windows)
    assert not windows.switches


@pytest.mark.parametrize("intervening_input", [None, "Winlogon"])
def test_manual_show_restores_only_its_own_input(child_api, tmp_path, intervening_input):
    windows = WindowState()

    def exit_shell():
        assert windows.visible == desktop.DESKTOP
        if intervening_input is not None:
            windows.visible = intervening_input

    windows.after_wait = exit_shell
    desktop.enter(tmp_path, tmp_path / "cadsg.exe", show=True, script=None,
                  windows=windows, output=lambda *a, **kw: None)
    assert windows.switches == ([desktop.DESKTOP] if intervening_input else [desktop.DESKTOP, desktop.DEFAULT])


def test_native_create_process_passes_absolute_desktop_and_no_handles(tmp_path):
    win = object.__new__(desktop.Windows)
    closed = []
    win.close_handle = lambda handle: closed.append(handle) or True

    def create(executable, command, process_sec, thread_sec, inherit, flags, env, workdir, startup, process):
        assert executable == "powershell.exe"
        assert command.value == "powershell.exe -NoProfile"
        assert startup._obj.cb == ctypes.sizeof(desktop.Startup)
        assert startup._obj.desktop == f"{desktop.STATION}\\{desktop.DESKTOP}"
        assert not inherit and not process_sec and not thread_sec
        assert flags == desktop.CREATE_NO_WINDOW | desktop.CREATE_UNICODE_ENVIRONMENT
        assert str(workdir) == str(tmp_path)
        assert "".join(env) == "A=1\0Z=2\0\0"
        process._obj.handle = 1
        process._obj.thread = 2
        process._obj.pid = 100
        return True

    win.create_process = create
    with win.shell(["powershell.exe", "-NoProfile"], {"Z": "2", "A": "1"}, tmp_path, show=False) as process:
        assert process.pid == 100 and closed == [2]
    assert closed == [2, 1]


@pytest.mark.skipif(os.name != "nt", reason="Windows last-error state")
def test_existing_desktop_is_not_adopted():
    win = object.__new__(desktop.Windows)
    win.open_desktop = lambda *args: 12
    closed = []
    win.close_desktop = closed.append
    win.create_desktop = lambda *args: pytest.fail("Must not create/attach")
    with pytest.raises(desktop.DesktopError, match="already exists"):
        with win.fresh_desktop(None, show=False):
            pytest.fail("Must not use or adopt an existing desktop")
    assert closed == [12]
