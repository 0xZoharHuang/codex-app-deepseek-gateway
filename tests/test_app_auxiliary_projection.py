"""Native observation projection under auxiliary churn; fake graph, no live processes."""
import copy
import pytest
from codex_app_deepseek import isolated_app as app
from test_app_control_plane import configured
from test_app_status_generations import generation


def observe(graph, native, environment, exits):
    return graph.backend.native_control_plane(
        graph.root, executable=native, expected_sha256=app.sha(native),
        environment=environment, auxiliary_exits=exits)


def two_native_with_git(monkeypatch, tmp_path):
    graph, native, environment = configured(monkeypatch, tmp_path)
    graph.rows[30]["ppid"] = 10
    graph.rows[31] = {**graph.rows[30], "pid": 31, "created": 121.0}
    graph.arguments[31] = list(graph.arguments[30])
    graph.environments[31] = copy.deepcopy(environment)
    graph.rows[60] = {**graph.rows[20], "pid": 60, "created": 115.0,
                      "ppid": 20, "exe": "git.exe"}
    graph.edges[10] = [20, 30, 31]
    graph.edges[20] = [60]
    graph.edges[31] = []
    graph.edges[60] = []
    return graph, native, environment


@pytest.mark.parametrize("stage", ["before_queue", "during_children", "final_edges"])
def test_git_exit_outside_both_native_chains_is_explicit_and_preserves_endpoints(
        monkeypatch, tmp_path, stage):
    graph, native, environment = two_native_with_git(monkeypatch, tmp_path)
    expected = [copy.deepcopy(graph.rows[pid]) for pid in (10, 20, 30, 31)]
    if stage == "before_queue":
        graph.hooks[("cmdline", 30)] = lambda: graph.rows.pop(60)
    elif stage == "during_children":
        graph.hooks[("children", 60)] = lambda: graph.rows.pop(60)
    else:
        original = graph.backend._require_live
        def require_live(identity, message):
            if identity["pid"] == 60 and message == "App process expired before readiness":
                graph.rows.pop(60)
            return original(identity, message)
        graph.backend._require_live = require_live
    exits = []
    assert observe(graph, native, environment, exits) == expected
    assert exits == [{"pid": 60, "created": 115.0, "ppid": 20,
                      "executable_name": "git.exe",
                      "reason": "natural_exit_outside_native_ancestry"}]
    assert graph.env_reads == [10, 30, 31, 30, 31, 10]
    assert [graph.rows[pid]["created"] for pid in (30, 31)] == [120.0, 121.0]


def test_generic_tree_and_unrequested_projection_remain_strict(monkeypatch, tmp_path):
    graph, native, environment = two_native_with_git(monkeypatch, tmp_path)
    graph.edges[30] = []
    graph.hooks[("children", 60)] = lambda: graph.rows.pop(60)
    with pytest.raises(app.psutil.NoSuchProcess) as error:
        graph.backend.descendants(graph.root)
    assert error.value.pid == 60
    (tmp_path / "second").mkdir()
    graph, native, environment = two_native_with_git(monkeypatch, tmp_path / "second")
    graph.hooks[("children", 60)] = lambda: graph.rows.pop(60)
    with pytest.raises(app.psutil.NoSuchProcess) as error:
        graph.backend.native_control_plane(graph.root, executable=native,
            expected_sha256=app.sha(native), environment=environment)
    assert error.value.pid == 60


@pytest.mark.parametrize("stage", ["before_native_admission", "after_native_admission"])
def test_disappearing_native_ancestor_never_becomes_optional(monkeypatch, tmp_path, stage):
    graph, native, environment = configured(monkeypatch, tmp_path)
    if stage == "before_native_admission":
        graph.hooks[("identity", 30)] = lambda: graph.rows.pop(20)
    else:
        graph.hooks[("cmdline", 30)] = lambda: graph.rows.pop(20)
    exits = []
    with pytest.raises(app.psutil.NoSuchProcess) as error:
        observe(graph, native, environment, exits)
    assert error.value.pid == 20
    assert exits == []


@pytest.mark.parametrize("pid", [10, 30, 31])
def test_root_and_each_identified_native_loss_still_fail(monkeypatch, tmp_path, pid):
    graph, native, environment = two_native_with_git(monkeypatch, tmp_path)
    graph.hooks[("children", 60)] = lambda: graph.rows.pop(pid)
    exits = []
    with pytest.raises((app.psutil.NoSuchProcess, app.LaunchError)):
        observe(graph, native, environment, exits)
    assert exits == []


@pytest.mark.parametrize("fault", ["reuse", "denied", "foreign_sid", "foreign_session",
                                   "elevated", "wrong_subject", "transient"])
def test_uncertainty_is_not_relabelled_as_an_auxiliary_exit(monkeypatch, tmp_path, fault):
    graph, native, environment = two_native_with_git(monkeypatch, tmp_path)
    if fault == "reuse":
        graph.hooks[("children", 60)] = lambda: graph.rows[60].update(created=900.0)
    elif fault in {"foreign_sid", "foreign_session"}:
        key, value = ("sid", "foreign") if fault == "foreign_sid" else ("session", 99)
        graph.rows[60][key] = value
        graph.hooks[("identity", 60)] = lambda: graph.rows.pop(60)
    elif fault == "elevated":
        graph.rows[60]["elevated"] = True
        graph.hooks[("children", 60)] = lambda: graph.rows.pop(60)
    else:
        original = graph.backend._require_live
        fired = False
        def require_live(identity, message):
            nonlocal fired
            if identity["pid"] == 60 and message == "owned App ancestor changed before observation" and not fired:
                fired = True
                if fault == "denied":
                    raise app.psutil.AccessDenied(60)
                raise app.psutil.NoSuchProcess(999 if fault == "wrong_subject" else 60)
            return original(identity, message)
        graph.backend._require_live = require_live
    exits = []
    with pytest.raises((app.psutil.Error, app.LaunchError)):
        observe(graph, native, environment, exits)
    assert exits == []


def test_all_native_candidates_stay_protected_even_if_not_app_server(monkeypatch, tmp_path):
    graph, native, environment = two_native_with_git(monkeypatch, tmp_path)
    graph.arguments[31] = [str(native), "--version"]
    graph.hooks[("children", 60)] = lambda: graph.rows.pop(31)
    with pytest.raises(app.psutil.NoSuchProcess) as error:
        observe(graph, native, environment, [])
    assert error.value.pid == 31


def test_exit_diagnostics_are_bounded_and_not_committed_on_failure(monkeypatch, tmp_path):
    graph, native, environment = configured(monkeypatch, tmp_path)
    pids = list(range(100, 165))
    for pid in pids:
        graph.rows[pid] = {**graph.rows[20], "pid": pid, "created": 115.0,
                           "ppid": 10, "exe": "git.exe"}
        graph.edges[pid] = []
        graph.hooks[("children", pid)] = lambda pid=pid: graph.rows.pop(pid)
    graph.edges[10] = [20, *pids]
    exits = []
    with pytest.raises(app.LaunchError, match="too many auxiliary exits"):
        observe(graph, native, environment, exits)
    assert exits == []


def test_status_reports_auxiliary_exit_without_rewriting_original_receipt(generation):
    run, _, _, _, backend = generation
    original = (run / "receipt.json").read_bytes()
    observe_original = backend.native_control_plane
    expected = {"pid": 60, "created": 115.0, "ppid": 10, "executable_name": "git.exe",
                "reason": "natural_exit_outside_native_ancestry"}
    def control_plane(root, **binding):
        binding["auxiliary_exits"].append(dict(expected))
        return observe_original(root, **binding)
    backend.native_control_plane = control_plane
    result = app.launch_status(run, backend)["current_backend_observation"]
    assert result["status"] == "verified_current_backends"
    assert result["excluded_auxiliary_processes"] == [expected]
    assert [item["pid"] for item in result["native_processes"]] == [21]
    assert result["native_processes"][0]["ancestor_chain"][-1]["pid"] == 10
    assert (run / "receipt.json").read_bytes() == original


def test_legitimate_auxiliary_diagnostic_never_masks_late_native_binding_failure(
        monkeypatch, tmp_path):
    graph, native, environment = two_native_with_git(monkeypatch, tmp_path)
    graph.hooks[("cmdline", 30)] = lambda: graph.rows.pop(60)
    original = graph.backend.selected_environment
    def selected(identity):
        result = original(identity)
        if identity["pid"] == 30 and graph.env_reads.count(30) == 1:
            graph.environments[30]["CODEX_HOME"] = "changed-after-initial-verification"
        return result
    graph.backend.selected_environment = selected
    exits = []
    with pytest.raises(app.LaunchError, match="native control-plane environment differs"):
        observe(graph, native, environment, exits)
    assert [item["pid"] for item in exits] == [60]
    assert graph.env_reads == [10, 30, 31, 30]


def test_exited_git_is_not_confused_with_last_inspected_live_git(monkeypatch, tmp_path):
    graph, native, environment = two_native_with_git(monkeypatch, tmp_path)
    graph.rows[61] = {**graph.rows[60], "pid": 61, "created": 116.0}
    graph.edges[20] = [60, 61]
    graph.edges[61] = []
    graph.hooks[("cmdline", 30)] = lambda: graph.rows.pop(60)
    live_git = copy.deepcopy(graph.rows[61])
    exits = []
    found = observe(graph, native, environment, exits)
    assert [row["pid"] for row in found] == [10, 20, 30, 31, 61]
    assert found[-1] == live_git
    assert [row["pid"] for row in exits] == [60]
    assert graph.env_reads == [10, 30, 31, 30, 31, 10]
