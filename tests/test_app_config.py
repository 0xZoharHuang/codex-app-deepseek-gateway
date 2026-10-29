from __future__ import annotations
import json
import os
from pathlib import Path
import stat
import subprocess
import tomllib

import pytest

from codex_app_deepseek.app_config import (
    PINNED_MODEL, PROVIDER_ID, ConfigurationError, ConfigurationConflict,
    prepare, apply, restore,
)


@pytest.fixture
def paths(tmp_path):
    root = tmp_path / "Chinese \u76ee\u5f55 with spaces"
    root.mkdir()
    catalog = root / "model catalog.json"
    catalog.write_text(json.dumps({"models": [{"slug": PINNED_MODEL}]}), encoding="utf-8")
    helper = root / "auth helper.exe"
    helper.write_bytes(b"test fixture; never executed")
    return root / "config.toml", catalog, helper, root / "private backups"


def prepared(paths):
    config, catalog, helper, _ = paths
    return prepare(config, catalog_path=catalog, auth_command=helper, port=18765)


def test_prepare_is_read_only_and_has_no_secrets_in_repr(paths):
    config, _, _, backups = paths
    config.write_text('# existing user setting\ncustom_key = "SENSITIVE_TEST_VALUE"\n', encoding="utf-8")
    original = config.read_bytes()
    result = prepared(paths)
    assert config.read_bytes() == original
    assert not backups.exists()
    assert "SENSITIVE_TEST_VALUE" not in repr(result)
    assert "rendered_text" not in repr(result)


def test_fresh_config_exact_provider_and_safe_defaults(paths):
    result = prepared(paths)
    data = tomllib.loads(result.rendered_text)
    assert data["model"] == PINNED_MODEL
    assert data["web_search"] == "live"
    assert data["model_provider"] == PROVIDER_ID
    assert data["model_catalog_json"] == str(paths[1])
    assert data["sandbox_mode"] == "workspace-write"
    assert data["approval_policy"] == "on-request"
    assert set(data["model_providers"][PROVIDER_ID]) == {"name", "base_url", "wire_api", "supports_websockets", "supports_standalone_web_search", "auth"}
    provider = data["model_providers"][PROVIDER_ID]
    assert provider["base_url"] == "http://127.0.0.1:18765/v1"
    assert provider["wire_api"] == "responses"
    assert provider["supports_websockets"] is False
    assert data["memories"] == {"extract_model": PINNED_MODEL, "consolidation_model": PINNED_MODEL}
    assert provider["auth"] == {"command": str(paths[2]), "args": ["auth-token"],
                               "timeout_ms": 30000, "refresh_interval_ms": 300000}
    assert data["features"] == {"standalone_web_search": True, "code_mode": True, "code_mode_only": True}
    assert provider["supports_standalone_web_search"] is True
    assert "permissions" not in data


def test_existing_tables_comments_and_permissions_preserved(paths):
    config, _, _, backups = paths
    original = (b'# user heading\r\nsandbox_mode = "read-only" # retain scope\r\n'
                b'approval_policy = "untrusted"\r\n'
                b'[features]\r\nmemories = true # remember this\r\nmulti_agent = true\r\n'
                b'[model_providers.keep]\r\nname = "Original" # do not remove\r\n'
                b'base_url = "https://example.invalid/v1"\r\n'
                b'[model_providers.keep.http_headers]\r\nX-Client = "keep"\r\n')
    config.write_bytes(original)
    result = prepared(paths)
    checkpoint = apply(result, backup_dir=backups)
    actual = config.read_text(encoding="utf-8")
    data = tomllib.loads(actual)
    assert data["sandbox_mode"] == "read-only"
    assert data["approval_policy"] == "untrusted"
    assert data["features"] == {"memories": True, "multi_agent": True, "standalone_web_search": True, "code_mode": True, "code_mode_only": True}
    assert data["model_providers"]["keep"]["http_headers"] == {"X-Client": "keep"}
    assert all(comment in actual for comment in ["# user heading", "# retain scope", "# remember this", "# do not remove"])
    assert data["model"] == PINNED_MODEL  # must remain top-level, never under the last provider table
    assert "model" not in data["model_providers"]["keep"]["http_headers"]
    restored = restore(checkpoint.manifest_path)
    assert not restored.removed_created_config
    assert config.read_bytes() == original


def test_existing_feature_and_permission_profiles_are_not_overridden(paths):
    config = paths[0]
    config.write_text('default_permissions = "restricted"\n[permissions.restricted]\nextends = ":read-only"\n'
                      '[features]\nmemories = false\nmulti_agent = false\n', encoding="utf-8")
    result = tomllib.loads(prepared(paths).rendered_text)
    assert result["default_permissions"] == "restricted"
    assert result["permissions"]["restricted"]["extends"] == ":read-only"
    assert result["features"] == {"memories": False, "multi_agent": False, "standalone_web_search": True, "code_mode": True, "code_mode_only": True}
    assert "sandbox_mode" not in result


def test_apply_and_restore_previously_absent_config_are_idempotent(paths):
    config, _, _, backups = paths
    first = apply(prepared(paths), backup_dir=backups)
    second = apply(prepared(paths), backup_dir=backups)
    assert first.changed and not second.changed
    restore(second.manifest_path)
    assert config.exists()
    restore(first.manifest_path)
    assert not config.exists()
    assert restore(first.manifest_path).removed_created_config


def test_later_user_edits_are_never_overwritten(paths):
    config, _, _, backups = paths
    checkpoint = apply(prepared(paths), backup_dir=backups)
    config.write_bytes(config.read_bytes() + b'\n# later user edit\n')
    edited = config.read_bytes()
    result = restore(checkpoint.manifest_path)
    assert not result.removed_created_config
    assert b"# later user edit" in config.read_bytes()
    assert tomllib.loads(config.read_text(encoding="utf8")) == {}


def test_prepare_apply_conflict_does_not_create_checkpoint(paths):
    config, _, _, backups = paths
    planned = prepared(paths)
    config.write_text('# concurrent editor\n', encoding="utf-8")
    with pytest.raises(ConfigurationConflict, match="after prepare"):
        apply(planned, backup_dir=backups)
    assert not backups.exists()
    assert config.read_text() == '# concurrent editor\n'


@pytest.mark.parametrize("existing_key", ["env_key", "experimental_bearer_token", "requires_openai_auth"])
def test_existing_conflicting_auth_refused(paths, existing_key):
    value = 'false' if existing_key == "requires_openai_auth" else '"do-not-print-secret"'
    paths[0].write_text(f'[model_providers.{PROVIDER_ID}]\n{existing_key} = {value}\n', encoding="utf-8")
    with pytest.raises(ConfigurationConflict) as error:
        prepared(paths)
    assert "do-not-print-secret" not in str(error.value)


@pytest.mark.parametrize("catalog_value", [None, "bad-json", {}, {"models": []}, {"models": [None]},
                                          {"models": [{"slug": "wrong"}]}])
def test_missing_or_incompatible_catalog_rejected(paths, catalog_value):
    catalog = paths[1]
    if catalog_value is None:
        catalog.unlink()
    elif catalog_value == "bad-json":
        catalog.write_text('{broken', encoding="utf-8")
    else:
        catalog.write_text(json.dumps(catalog_value), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="[Cc]atalog"):
        prepared(paths)
    assert not paths[0].exists()


@pytest.mark.parametrize("port", [0, 65536, -1, True, "18765"])
def test_invalid_ports_fail(paths, port):
    with pytest.raises(ConfigurationError, match="port"):
        prepare(paths[0], catalog_path=paths[1], auth_command=paths[2], port=port)


def test_missing_helper_and_invalid_config_fail_without_launching(paths, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("prepare must not run a process"))
    result = prepared(paths)
    assert result.changed
    paths[0].write_text('[bad syntax', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="valid UTF-8 TOML"):
        prepared(paths)
    paths[0].unlink()
    paths[2].unlink()
    with pytest.raises(ConfigurationError, match="absolute executable"):
        prepared(paths)


def test_corrupt_backup_refuses_restore(paths):
    config, _, _, backups = paths
    config.write_bytes(b'# original bytes\n')
    checkpoint = apply(prepared(paths), backup_dir=backups)
    before = config.read_bytes()
    (checkpoint.manifest_path.parent / "original.toml").write_bytes(b'tampered')
    with pytest.raises(ConfigurationError, match="Backup hash mismatch"):
        restore(checkpoint.manifest_path)
    assert config.read_bytes() == before


def test_checkpoint_contains_hashes_and_existence_but_no_config_contents(paths):
    paths[0].write_text('unrelated = "SENSITIVE_TEST_VALUE"\n', encoding="utf-8")
    checkpoint = apply(prepared(paths), backup_dir=paths[3])
    raw = checkpoint.manifest_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["state"] == "applied" and data["was_present"] is True
    assert len(data["before_sha256"]) == len(data["applied_sha256"]) == 64
    assert "SENSITIVE_TEST_VALUE" not in raw
    assert "SENSITIVE_TEST_VALUE" not in repr(checkpoint)


def test_bom_and_crlf_restore_byte_exact(paths):
    original = b'\xef\xbb\xbf# bom comment\r\ncustom = 3\r\n'
    paths[0].write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=paths[3])
    assert paths[0].read_bytes().startswith(b'\xef\xbb\xbf')
    restore(checkpoint.manifest_path)
    assert paths[0].read_bytes() == original


@pytest.mark.parametrize("resource", ["directory", "manifest", "config", "backup", "restore_input", "restored_output"])
def test_backup_directory_is_private_on_windows(paths, resource):
    paths[0].write_text("# original config\n", encoding="utf-8")
    checkpoint = apply(prepared(paths), backup_dir=paths[3])
    if resource in {"restore_input", "restored_output"}:
        restore(checkpoint.manifest_path)
    private_path = {"directory": checkpoint.manifest_path.parent, "manifest": checkpoint.manifest_path,
                    "config": paths[0], "backup": checkpoint.manifest_path.parent / "original.toml",
                    "restore_input": checkpoint.manifest_path.parent / "restore-input.toml",
                    "restored_output": checkpoint.manifest_path.parent / "restored.toml"}[resource]
    if os.name != "nt":
        assert stat.S_IMODE(private_path.stat().st_mode) == (0o700 if resource == "directory" else 0o600)
        return
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p)]
    api.GetNamedSecurityInfoW.restype = wintypes.DWORD
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, wintypes.DWORD,
        wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    api.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    descriptor = ctypes.c_void_p()
    assert api.GetNamedSecurityInfoW(str(private_path), 1, 4, None, None, None, None,
                                     ctypes.byref(descriptor)) == 0
    output = wintypes.LPWSTR()
    try:
        assert api.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(output), None)
        sddl = output.value
        assert sddl.startswith("D:P")  # protected from inherited broad directory grants
        assert sddl.count("(A;") == 2
        assert ";;;SY)" in sddl
        assert ";;;WD)" not in sddl and ";;;BU)" not in sddl and ";;;AU)" not in sddl
    finally:
        if output: kernel.LocalFree(output)
        kernel.LocalFree(descriptor)


def test_catalog_changes_after_prepare_block_apply(paths):
    result = prepared(paths)
    paths[1].write_text('{"models": []}', encoding="utf-8")
    with pytest.raises(ConfigurationConflict, match="Catalog changed"):
        apply(result, backup_dir=paths[3])
    assert not paths[0].exists()
    assert not paths[3].exists()


def test_auth_json_cannot_be_configuration_target(paths):
    with pytest.raises(ConfigurationError, match="authentication stores"):
        prepare(paths[0].with_name("auth.json"), catalog_path=paths[1], auth_command=paths[2], port=18765)


def test_auxiliary_models_routed_without_changing_memory_policy_and_restored(paths):
    config, _, _, backups = paths
    original = ("# memory policy stays\n[memories]\n"
                'extract_model = "gpt-5.6-luna" # former extraction\n'
                'consolidation_model = "gpt-5.6-terra"\n'
                'generate_memories = false\nuse_memories = false\n'
                'max_raw_memories_for_consolidation = 37\n'
                'min_rollout_idle_hours = 9\n'
                '[features]\nmemories = true\n')
    config.write_text(original, encoding="utf-8", newline="")
    before = config.read_bytes()
    plan = prepared(paths)
    parsed = tomllib.loads(plan.rendered_text)
    assert parsed["memories"]["extract_model"] == PINNED_MODEL
    assert parsed["memories"]["consolidation_model"] == PINNED_MODEL
    assert parsed["memories"]["generate_memories"] is False
    assert parsed["memories"]["use_memories"] is False
    assert parsed["memories"]["max_raw_memories_for_consolidation"] == 37
    assert parsed["memories"]["min_rollout_idle_hours"] == 9
    assert parsed["features"]["memories"] is True
    assert "# memory policy stays" in plan.rendered_text
    assert "# former extraction" in plan.rendered_text
    assert {"memories.extract_model", "memories.consolidation_model"} <= set(plan.changed_settings)
    checkpoint = apply(plan, backup_dir=backups)
    assert checkpoint.changed_settings == plan.changed_settings
    manifest = json.loads(checkpoint.manifest_path.read_text(encoding="utf-8"))
    assert manifest["changed_settings"] == list(plan.changed_settings)
    restore(checkpoint.manifest_path)
    assert config.read_bytes() == before


def test_existing_websocket_choice_is_explicitly_changed_and_restorable(paths):
    config, _, _, backups = paths
    original = f'[model_providers.{PROVIDER_ID}]\nsupports_websockets = true # prior choice\n'
    config.write_text(original, encoding="utf-8", newline="")
    before = config.read_bytes()
    plan = prepared(paths)
    assert tomllib.loads(plan.rendered_text)["model_providers"][PROVIDER_ID]["supports_websockets"] is False
    assert f"model_providers.{PROVIDER_ID}.supports_websockets" in plan.changed_settings
    assert "# prior choice" in plan.rendered_text
    checkpoint = apply(plan, backup_dir=backups)
    repeated = prepared(paths)
    assert not repeated.changed
    assert repeated.changed_settings == ()
    restore(checkpoint.manifest_path)
    assert config.read_bytes() == before


def test_invalid_memory_table_refused_without_mutation(paths):
    paths[0].write_text('memories = "not a table"\n', encoding="utf-8")
    original = paths[0].read_bytes()
    with pytest.raises(ConfigurationError, match="memories must be"):
        prepared(paths)
    assert paths[0].read_bytes() == original


def test_web_search_live_is_explicit_and_original_mode_restored(paths):
    paths[0].write_text('web_search = "cached" # former index\n', encoding='utf8')
    original = paths[0].read_bytes()
    plan = prepared(paths)
    assert tomllib.loads(plan.rendered_text)["web_search"] == "live"
    assert "web_search" in plan.changed_settings
    checkpoint = apply(plan, backup_dir=paths[3])
    manifest = json.loads(checkpoint.manifest_path.read_text(encoding="utf8"))
    assert manifest["schema_version"] == 2
    assert (checkpoint.manifest_path.parent / "applied.toml").read_bytes() == paths[0].read_bytes()
    restore(checkpoint.manifest_path)
    assert paths[0].read_bytes() == original


def test_three_way_preserves_project_settings_and_comments(paths):
    config, _, _, backups = paths
    cp = apply(prepared(paths), backup_dir=backups)
    with config.open('a', encoding='utf8') as f:
        f.write('\n# retained comment\n[projects."D:/Work"]\ntrust_level = "trusted"\n')
    result = restore(cp.manifest_path)
    assert not result.removed_created_config
    text = config.read_text(encoding='utf8')
    assert '# retained comment' in text
    assert tomllib.loads(text) == {'projects': {'D:/Work': {'trust_level': 'trusted'}}}
    assert restore(cp.manifest_path).restored_sha256 == result.restored_sha256


def test_three_way_preserves_nonowned_memory_and_auth_fields(paths):
    config, _, _, backups = paths
    config.write_text(f'[memories]\ngenerate_memories = false\n[model_providers.{PROVIDER_ID}.auth]\ncustom_note = "keep"\n', encoding='utf8')
    cp = apply(prepared(paths), backup_dir=backups)
    import tomlkit
    data = tomlkit.parse(config.read_text(encoding='utf8'))
    data['memories']['generate_memories'] = True
    data['model_providers'][PROVIDER_ID]['auth']['custom_note'] = 'new-user-note'
    config.write_text(tomlkit.dumps(data), encoding='utf8')
    restore(cp.manifest_path)
    result = tomllib.loads(config.read_text(encoding='utf8'))
    assert result == {'memories': {'generate_memories': True},
                      'model_providers': {PROVIDER_ID: {'auth': {'custom_note': 'new-user-note'}}}}


@pytest.mark.parametrize('field,change', [('model', 'different'), ('memories.extract_model', 'different'),
                                         ('model_providers.codex_app_deepseek.auth.command', 'different')])
def test_owned_leaf_changes_conflict_without_partial_restore(paths, field, change):
    config, _, _, backups = paths
    cp = apply(prepared(paths), backup_dir=backups)
    import tomlkit
    data = tomlkit.parse(config.read_text(encoding='utf8'))
    table = data
    parts = field.split('.')
    for part in parts[:-1]: table = table[part]
    table[parts[-1]] = change
    config.write_text(tomlkit.dumps(data), encoding='utf8'); before = config.read_bytes()
    with pytest.raises(ConfigurationConflict, match='owned setting'):
        restore(cp.manifest_path)
    assert config.read_bytes() == before
    assert json.loads(cp.manifest_path.read_text(encoding='utf8'))['state'] == 'applied'


def test_changed_parent_shape_is_not_treated_as_absent(paths):
    config, _, _, backups = paths
    cp = apply(prepared(paths), backup_dir=backups)
    import tomlkit
    data = tomlkit.parse(config.read_text(encoding='utf8'));data['memories'] = 'do not replace'
    config.write_text(tomlkit.dumps(data), encoding='utf8'); before = config.read_bytes()
    with pytest.raises(ConfigurationConflict, match='parent table'):
        restore(cp.manifest_path)
    assert config.read_bytes() == before


@pytest.mark.parametrize('tamper', ['snapshot', 'owned-paths', 'unowned-change'])
def test_checkpoint_tampering_does_not_broaden_owned_settings(paths, tamper):
    config, _, _, backups = paths
    config.write_text('user_setting = "original"\n', encoding='utf8')
    cp = apply(prepared(paths), backup_dir=backups)
    manifest = json.loads(cp.manifest_path.read_text(encoding='utf8'))
    applied = cp.manifest_path.parent/'applied.toml'
    if tamper == 'snapshot': applied.write_bytes(applied.read_bytes() + b'\n# corrupt')
    elif tamper == 'owned-paths': manifest['changed_settings'].append('user_setting')
    else:
        import hashlib
        applied.write_text(applied.read_text(encoding='utf8').replace('user_setting = "original"', 'user_setting = "altered"'), encoding='utf8')
        manifest['applied_sha256'] = hashlib.sha256(applied.read_bytes()).hexdigest()
    cp.manifest_path.write_text(json.dumps(manifest),encoding='utf8'); before = config.read_bytes()
    with pytest.raises(ConfigurationError):restore(cp.manifest_path)
    assert config.read_bytes() == before


def test_missing_owned_value_that_was_originally_absent_is_already_restored(paths):
    config, _, _, backups = paths
    cp = apply(prepared(paths), backup_dir=backups)
    import tomlkit
    data = tomlkit.parse(config.read_text(encoding='utf8'));del data['model']
    config.write_text(tomlkit.dumps(data), encoding='utf8')
    restore(cp.manifest_path)
    assert not config.exists()


def test_existing_owned_value_removed_is_conflict(paths):
    config, _, _, backups = paths
    config.write_text('model = "prior"\n',encoding='utf8')
    cp = apply(prepared(paths), backup_dir=backups)
    import tomlkit
    data = tomlkit.parse(config.read_text(encoding='utf8'));del data['model']
    config.write_text(tomlkit.dumps(data), encoding='utf8');before = config.read_bytes()
    with pytest.raises(ConfigurationConflict):restore(cp.manifest_path)
    assert config.read_bytes() == before


def test_schema1_keeps_strict_later_edit_check(paths):
    config, _, _, backups = paths
    cp = apply(prepared(paths), backup_dir=backups)
    manifest = json.loads(cp.manifest_path.read_text(encoding='utf8'));manifest['schema_version']=1
    cp.manifest_path.write_text(json.dumps(manifest),encoding='utf8')
    config.write_bytes(config.read_bytes()+b'\n# keep\n');before=config.read_bytes()
    with pytest.raises(ConfigurationConflict):restore(cp.manifest_path)
    assert config.read_bytes()==before


def test_restore_result_hash_tracks_preserved_edits(paths):
    config, _, _, backups = paths
    cp=apply(prepared(paths), backup_dir=backups)
    config.write_bytes(config.read_bytes()+b'\n# keep me\n')
    restored=restore(cp.manifest_path)
    import hashlib
    assert restored.restored_sha256==hashlib.sha256(config.read_bytes()).hexdigest()
    assert restore(cp.manifest_path)==restored
    config.write_bytes(config.read_bytes()+b'# newer edit\n');before=config.read_bytes()
    with pytest.raises(ConfigurationConflict,match='after restore'):restore(cp.manifest_path)
    assert config.read_bytes()==before


def test_restore_preserves_bom_crlf_with_new_user_content(paths):
    config, _, _, backups=paths
    config.write_bytes(b'\xef\xbb\xbf# heading\r\nprior = 3\r\n')
    cp=apply(prepared(paths),backup_dir=backups)
    config.write_bytes(config.read_bytes()+b'\r\n# later\r\n[desktop]\nfont = 14\r\n')
    restore(cp.manifest_path)
    raw=config.read_bytes();assert raw.startswith(b'\xef\xbb\xbf')
    assert b'\n' not in raw.replace(b'\r\n',b'')
    assert tomllib.loads(raw.decode('utf-8-sig'))=={'prior':3,'desktop':{'font':14}}


def test_restore_race_refuses_write(paths,monkeypatch):
    from codex_app_deepseek import app_config
    config, _, _, backups=paths
    cp=apply(prepared(paths),backup_dir=backups)
    original=app_config._three_way_bytes
    def racing(*args,**kwargs):
        value=original(*args,**kwargs);config.write_bytes(config.read_bytes()+b'\n# concurrent\n');return value
    monkeypatch.setattr(app_config,'_three_way_bytes',racing)
    with pytest.raises(ConfigurationConflict,match='during restore'):restore(cp.manifest_path)
    assert b'# concurrent' in config.read_bytes()


def test_bool_and_integer_owned_values_are_not_equivalent(paths):
    config, _, _, backups = paths
    cp = apply(prepared(paths), backup_dir=backups)
    config.write_text(config.read_text(encoding='utf8').replace('standalone_web_search = true', 'standalone_web_search = 1'), encoding='utf8')
    before = config.read_bytes()
    with pytest.raises(ConfigurationConflict): restore(cp.manifest_path)
    assert config.read_bytes() == before


def test_dotted_literal_key_cannot_impersonate_owned_path(paths):
    from codex_app_deepseek.app_config import _three_way_bytes
    original = b''
    applied = b'"memories.extract_model" = "anything"\n'
    with pytest.raises(ConfigurationError, match='unowned'):
        _three_way_bytes(original, applied, applied, ['memories.extract_model'])


def test_rollback_preserves_user_later_empty_tables(paths):
    config, _, _, backups = paths
    cp = apply(prepared(paths), backup_dir=backups)
    config.write_bytes(config.read_bytes() + b'\n[user_later_empty]\n[model_providers.user_later_empty]\n')
    restore(cp.manifest_path)
    assert tomllib.loads(config.read_text(encoding='utf8')) == {
        'user_later_empty': {}, 'model_providers': {'user_later_empty': {}}}


def test_rollback_preserves_user_later_empty_inline_table(paths):
    config, _, _, backups = paths
    cp = apply(prepared(paths), backup_dir=backups)
    # A top-level inline table must precede the generated table headers.
    config.write_bytes(b'user_later_inline = {}\n' + config.read_bytes())
    restore(cp.manifest_path)
    assert tomllib.loads(config.read_text(encoding='utf8')) == {'user_later_inline': {}}


@pytest.mark.parametrize('old,new,owned', [
    ('supports_websockets = false', 'supports_websockets = 0', 'model_providers.codex_app_deepseek.supports_websockets'),
    ('supports_standalone_web_search = true', 'supports_standalone_web_search = 1', 'model_providers.codex_app_deepseek.supports_standalone_web_search'),
    ('standalone_web_search = true', 'standalone_web_search = 1', 'features.standalone_web_search'),
    ('timeout_ms = 30000', 'timeout_ms = 30000.0', 'model_providers.codex_app_deepseek.auth'),
])
def test_prepare_tracks_type_only_changes_for_reversible_checkpoint(paths, old, new, owned):
    config, _, _, backups = paths
    config.write_text(prepared(paths).rendered_text, encoding='utf8')
    config.write_text(config.read_text(encoding='utf8').replace(old, new), encoding='utf8')
    original = config.read_bytes()
    plan = prepared(paths)
    assert owned in plan.changed_settings
    cp = apply(plan, backup_dir=backups)
    restore(cp.manifest_path)
    assert config.read_bytes() == original


def test_type_equal_is_recursive_for_nested_collections():
    from codex_app_deepseek.app_config import _equal
    assert not _equal({'x': [{'y': True}]}, {'x': [{'y': 1}]})
    assert not _equal({'x': 30000}, {'x': 30000.0})
    assert _equal({'x': [{'y': True}]}, {'x': [{'y': True}]})


def test_empty_unowned_table_snapshot_change_is_not_invisible():
    from codex_app_deepseek.app_config import _three_way_bytes
    with pytest.raises(ConfigurationError, match='unowned'):
        _three_way_bytes(None, b'model="pinned"\n[unowned_empty]\n',
                         b'model="pinned"\n[unowned_empty]\n', ['model'])


@pytest.mark.parametrize('change', ['scalar', 'empty_table', 'dotted_literal', 'typed_value'])
def test_restored_checkpoint_still_validates_entire_applied_snapshot(paths, change):
    import hashlib
    config, _, _, backups = paths
    config.write_text('user_value = 1\n', encoding='utf8')
    checkpoint = apply(prepared(paths), backup_dir=backups)
    manifest = json.loads(checkpoint.manifest_path.read_text(encoding='utf8'))
    applied_file = checkpoint.manifest_path.parent / 'applied.toml'
    applied = applied_file.read_bytes()
    if change == 'scalar':
        applied = applied.replace(b'user_value = 1', b'user_value = 2')
    elif change == 'typed_value':
        applied = applied.replace(b'user_value = 1', b'user_value = true')
    elif change == 'empty_table':
        applied += b'\n[unowned_empty]\n'
    else:
        applied = b'"memories.extract_model" = "unowned literal"\n' + applied
    applied_file.write_bytes(applied)
    config.write_bytes(applied)
    manifest.update(state='restored', applied_sha256=hashlib.sha256(applied).hexdigest(),
                    restored_sha256=hashlib.sha256(applied).hexdigest())
    checkpoint.manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    before = checkpoint.manifest_path.read_bytes(), config.read_bytes()
    with pytest.raises(ConfigurationError, match='unowned'):
        restore(checkpoint.manifest_path)
    assert (checkpoint.manifest_path.read_bytes(), config.read_bytes()) == before


def _remove_restore_evidence(manifest):
    for key in ('restore_input_file', 'restore_input_sha256', 'restored_file', 'restore_prepared_at'):
        manifest.pop(key, None)


@pytest.mark.parametrize('schema', [1, 2])
@pytest.mark.parametrize('original', [None, b'# original\nuser_value = 4\n'])
def test_legacy_restored_checkpoint_accepts_byte_exact_original(paths, schema, original):
    config, _, _, backups = paths
    if original is not None:
        config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    if schema == 1:
        manifest = json.loads(checkpoint.manifest_path.read_text(encoding='utf8'))
        manifest['schema_version'] = 1
        checkpoint.manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    expected = restore(checkpoint.manifest_path)
    manifest = json.loads(checkpoint.manifest_path.read_text(encoding='utf8'))
    _remove_restore_evidence(manifest)
    checkpoint.manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    assert restore(checkpoint.manifest_path) == expected
    assert (config.read_bytes() if config.exists() else None) == original


@pytest.mark.parametrize('schema', [1, 2])
def test_legacy_restored_checkpoint_cannot_self_certify_an_unproved_hash(paths, schema):
    import hashlib
    config, _, _, backups = paths
    checkpoint = apply(prepared(paths), backup_dir=backups)
    manifest = json.loads(checkpoint.manifest_path.read_text(encoding='utf8'))
    manifest.update(schema_version=schema, state='restored',
                    restored_sha256=hashlib.sha256(config.read_bytes()).hexdigest())
    _remove_restore_evidence(manifest)
    checkpoint.manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    before = checkpoint.manifest_path.read_bytes(), config.read_bytes()
    with pytest.raises(ConfigurationError, match='evidence'):
        restore(checkpoint.manifest_path)
    assert (checkpoint.manifest_path.read_bytes(), config.read_bytes()) == before


@pytest.mark.parametrize('headers', [
    b'[model_providers.codex_app_deepseek.auth] # keep\n',
    b'[model_providers] # keep\n[model_providers.codex_app_deepseek.auth] # keep\n',
])
def test_rollback_moves_only_explicit_header_comments_once(headers):
    from codex_app_deepseek.app_config import _three_way_bytes
    applied = b'[model_providers.codex_app_deepseek.auth]\ncommand="helper"\n'
    current = headers + b'command="helper"\n'
    result = _three_way_bytes(None, applied, current, ['model_providers.codex_app_deepseek.auth'])
    assert result.count(b'# keep') == headers.count(b'# keep')
    assert tomllib.loads(result.decode('utf8')) == {}


@pytest.mark.parametrize('original', [None, b'user_value = 4\n'])
def test_legacy_restored_checkpoint_preserves_verified_partial_restore(paths, original):
    config, _, _, backups = paths
    if original is not None:
        config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    config.write_bytes(config.read_bytes() + b'\n# later user comment\n[projects."D:/work"]\ntrust_level="trusted"\n')
    expected = restore(checkpoint.manifest_path)
    before = config.read_bytes()
    manifest = json.loads(checkpoint.manifest_path.read_text(encoding='utf8'))
    _remove_restore_evidence(manifest)
    checkpoint.manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    assert restore(checkpoint.manifest_path) == expected
    assert config.read_bytes() == before


@pytest.mark.parametrize('later_edit', [False, True])
def test_rollback_keeps_unchanged_nan_and_other_float_values(paths, later_edit):
    config, _, _, backups = paths
    original = b'user_nan = nan\n[user]\nvalues = [nan, +inf, -inf, 0.0, -0.0]\n'
    config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    if later_edit:
        config.write_bytes(config.read_bytes() + b'\n# later user edit\n')
    restore(checkpoint.manifest_path)
    result = config.read_bytes()
    if not later_edit:
        assert result == original
    else:
        import math
        data = tomllib.loads(result.decode("utf8"))
        assert set(data) == {"user_nan", "user"}
        assert math.isnan(data["user_nan"])
        assert set(data["user"]) == {"values"}
        values = data["user"]["values"]
        assert len(values) == 5 and math.isnan(values[0])
        assert values[1:3] == [math.inf, -math.inf]
        assert values[3:] == [0.0, -0.0]
        assert math.copysign(1.0, values[3]) == 1.0
        assert math.copysign(1.0, values[4]) == -1.0
        assert result.count(b"# later user edit") == 1


@pytest.mark.parametrize('before,after', [('0.0', '-0.0'), ('-0.0', '0.0')])
@pytest.mark.parametrize('state', ['applied', 'restored'])
def test_signed_zero_snapshot_change_cannot_bypass_owned_scope(paths, before, after, state):
    import hashlib
    config, _, _, backups = paths
    config.write_text('user_zero = ' + before + '\n', encoding='utf8')
    checkpoint = apply(prepared(paths), backup_dir=backups)
    manifest = json.loads(checkpoint.manifest_path.read_text(encoding='utf8'))
    snapshot = checkpoint.manifest_path.parent / 'applied.toml'
    changed = snapshot.read_bytes().replace(('user_zero = ' + before).encode(),
                                            ('user_zero = ' + after).encode())
    snapshot.write_bytes(changed)
    config.write_bytes(changed)
    manifest.update(applied_sha256=hashlib.sha256(changed).hexdigest(), state=state)
    if state == 'restored':
        manifest['restored_sha256'] = hashlib.sha256(changed).hexdigest()
    checkpoint.manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    prior = config.read_bytes(), checkpoint.manifest_path.read_bytes()
    with pytest.raises(ConfigurationError, match='unowned'):
        restore(checkpoint.manifest_path)
    assert (config.read_bytes(), checkpoint.manifest_path.read_bytes()) == prior


def test_explicit_auth_arguments_are_owned_and_restore_exact_previous_command(paths):
    config,catalog,helper,backups=paths
    original=b'[model_providers.codex_app_deepseek.auth]\ncommand="previous helper"\nargs=["old", "argument"]\nextra="retain"\n'
    config.write_bytes(original)
    args=['-I','-m','codex_app_deepseek.cli','auth-token']
    planned=prepare(config,catalog_path=catalog,auth_command=helper,port=18765,auth_args=args)
    args.append('caller-later-change')
    assert tomllib.loads(planned.rendered_text)['model_providers'][PROVIDER_ID]['auth']['args']==['-I','-m','codex_app_deepseek.cli','auth-token']
    assert 'model_providers.'+PROVIDER_ID+'.auth' in planned.changed_settings
    checkpoint=apply(planned,backup_dir=backups);restore(checkpoint.manifest_path)
    assert config.read_bytes()==original


@pytest.mark.parametrize('args',['auth-token',{'flag':'value'},[1],[None],['bad\x00argument']])
def test_invalid_auth_argument_shapes_fail_without_writing_config(paths,args):
    config,catalog,helper,backups=paths
    with pytest.raises(ConfigurationError,match='auth_args'):
        prepare(config,catalog_path=catalog,auth_command=helper,port=18765,auth_args=args)
    assert not config.exists() and not backups.exists()


@pytest.mark.parametrize("selection", [
    {"code_mode": False, "code_mode_only": False},
    {"code_mode": True, "code_mode_only": False},
    {"code_mode": True, "code_mode_only": True},
    {"code_mode": False},
    {"code_mode_only": True},
])
def test_explicit_native_tool_mode_choices_and_rollback_are_preserved(paths, selection):
    config, _, _, backups = paths
    original = "[features]\n" + "".join(f"{k} = {str(v).lower()}\n" for k, v in selection.items())
    config.write_text(original, encoding="utf-8")
    before = config.read_bytes()
    plan = prepared(paths)
    parsed = tomllib.loads(plan.rendered_text)
    actual = {k: v for k, v in parsed["features"].items() if k in {"code_mode", "code_mode_only"}}
    assert actual == selection
    saved = apply(plan, backup_dir=backups)
    restore(saved.manifest_path)
    assert config.read_bytes() == before


def test_implicit_native_mode_is_explicit_default_and_rollback_removes_added_keys(paths):
    config, _, _, backups = paths
    config.write_bytes(b"# keep original\n[features]\nmemories = false\n")
    before = config.read_bytes()
    plan = prepared(paths)
    parsed = tomllib.loads(plan.rendered_text)
    assert parsed["features"]["code_mode"] is True and parsed["features"]["code_mode_only"] is True
    saved = apply(plan, backup_dir=backups)
    restore(saved.manifest_path)
    assert config.read_bytes() == before


@pytest.mark.parametrize('original', [None, b'', b'# original comment\n', b'user_value = 4\n'],
                         ids=['absent', 'empty', 'comments', 'unmanaged'])
@pytest.mark.parametrize('current_state', ['deleted', 'empty', 'applied'])
@pytest.mark.parametrize('hash_kind', ['none', 'current'])
def test_restored_checkpoint_proof_preserves_original_file_existence(paths, original, current_state, hash_kind):
    import hashlib
    config, _, _, backups = paths
    if original is not None:
        config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    if current_state == 'deleted':
        config.unlink()
    elif current_state == 'empty':
        config.write_bytes(b'')
    current = config.read_bytes() if config.exists() else None
    manifest = json.loads(checkpoint.manifest_path.read_text(encoding='utf8'))
    manifest.update(state='restored', restored_sha256=(
        hashlib.sha256(current).hexdigest() if hash_kind == 'current' and current is not None else None))
    checkpoint.manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    prior = checkpoint.manifest_path.read_bytes(), current
    # Existence is part of B: an existing original cannot be restored as a missing file.
    # An existing empty C can be a valid fixed point when B existed; retain later user removals.
    valid = ((current is None and original is None) or
             (current == b'' and original is not None and hash_kind == 'current'))
    if valid:
        result = restore(checkpoint.manifest_path)
        assert result.removed_created_config is (current is None)
    else:
        with pytest.raises(ConfigurationError):
            restore(checkpoint.manifest_path)
    assert checkpoint.manifest_path.read_bytes() == prior[0]
    assert (config.read_bytes() if config.exists() else None) == prior[1]


@pytest.mark.parametrize('original', [b'', b'# original comment\n', b'user_value = 4\n'],
                         ids=['empty', 'comments', 'unmanaged'])
def test_applied_checkpoint_refuses_removed_original_without_owned_leaves(paths, original):
    config, _, _, backups = paths
    config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    config.unlink()
    prior = checkpoint.manifest_path.read_bytes()
    with pytest.raises(ConfigurationConflict, match='removed after apply'):
        restore(checkpoint.manifest_path)
    assert not config.exists()
    assert checkpoint.manifest_path.read_bytes() == prior


@pytest.mark.parametrize('original', [None, b'', b'# original comment\n', b'user_value = 4\n'],
                         ids=['absent', 'empty', 'comments', 'unmanaged'])
def test_restore_without_original_owned_leaves_is_byte_exact_and_idempotent(paths, original):
    config, _, _, backups = paths
    if original is not None:
        config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    result = restore(checkpoint.manifest_path)
    assert (config.read_bytes() if config.exists() else None) == original
    assert result.removed_created_config is (original is None)
    assert restore(checkpoint.manifest_path) == result
    assert (config.read_bytes() if config.exists() else None) == original


@pytest.mark.parametrize('state', ['restore_prepared', 'restored'])
@pytest.mark.parametrize('original', [b'', b'# original comment\n', b'user_value = 4\n'],
                         ids=['empty', 'comments', 'unmanaged'])
def test_restore_evidence_cannot_certify_missing_original_file(paths, state, original):
    config, _, _, backups = paths
    config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    config.unlink()
    manifest = json.loads(checkpoint.manifest_path.read_text(encoding='utf8'))
    manifest.update(state=state, restore_input_file=None, restore_input_sha256=None,
                    restored_file=None, restored_sha256=None, restore_prepared_at='2026-10-26T00:00:00Z')
    checkpoint.manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    prior = checkpoint.manifest_path.read_bytes()
    with pytest.raises(ConfigurationConflict, match='removed after apply'):
        restore(checkpoint.manifest_path)
    assert not config.exists()
    assert checkpoint.manifest_path.read_bytes() == prior


@pytest.mark.parametrize('snapshot', ['original', 'applied'])
@pytest.mark.parametrize('corruption', ['missing', 'changed'])
def test_missing_file_restored_checkpoint_still_requires_verified_snapshots(paths, snapshot, corruption):
    config, _, _, backups = paths
    config.write_bytes(b'')
    checkpoint = apply(prepared(paths), backup_dir=backups)
    config.unlink()
    manifest = json.loads(checkpoint.manifest_path.read_text(encoding='utf8'))
    manifest.update(state='restored', restored_sha256=None)
    checkpoint.manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    proof = checkpoint.manifest_path.parent / (snapshot + '.toml')
    if corruption == 'missing':
        proof.unlink()
    else:
        proof.write_bytes(proof.read_bytes() + b'\n# changed proof\n')
    prior = checkpoint.manifest_path.read_bytes()
    with pytest.raises(ConfigurationError):
        restore(checkpoint.manifest_path)
    assert not config.exists()
    assert checkpoint.manifest_path.read_bytes() == prior


@pytest.mark.parametrize("original", [None, b"", b"# original file comment\n"])
@pytest.mark.parametrize("annotation", ["scalar", "array", "provider", "header"])
def test_later_owned_comments_survive_restore_without_changing_the_owned_value(paths, original, annotation):
    config, _, _, backups = paths
    if original is not None:
        config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    current = config.read_text(encoding="utf-8")
    note = "# later owned comment"
    if annotation == "scalar":
        lines = current.splitlines(keepends=True)
        index = next(i for i, line in enumerate(lines) if line.startswith("model = "))
        lines[index] = lines[index].rstrip("\r\n") + " " + note + "\n"
        current = "".join(lines)
    elif annotation == "array":
        assert 'args = ["auth-token"]' in current
        current = current.replace('args = ["auth-token"]',
            'args = [\n  "auth-token", # later owned comment\n  # later array comment\n]', 1)
    elif annotation == "provider":
        assert 'name = "Codex App DeepSeek Gateway"' in current
        current = current.replace('name = "Codex App DeepSeek Gateway"',
            'name = "Codex App DeepSeek Gateway" ' + note, 1)
    else:
        current = current.replace("[features]", "[features] ## later owned comment", 1)
    # The comment is the only later edit; every TOML setting is exactly the applied value.
    assert tomllib.loads(current) == tomllib.loads(checkpoint.config_path.read_text(encoding="utf-8"))
    config.write_text(current, encoding="utf-8")
    restore(checkpoint.manifest_path)
    assert config.is_file(), "Rollback removed the user's later comment with the created file"
    restored = config.read_text(encoding="utf-8")
    assert tomllib.loads(restored) == (tomllib.loads(original.decode()) if original is not None else {})
    assert "later owned comment" in restored
    if annotation == "header":
        assert "## later owned comment" in restored
    if annotation == "array":
        assert "# later array comment" in restored
    if original:
        assert "# original file comment" in restored
    once = config.read_bytes()
    restore(checkpoint.manifest_path)
    assert config.read_bytes() == once


def test_later_owned_duplicate_comments_keep_multiplicity(paths):
    config, _, _, backups = paths
    checkpoint = apply(prepared(paths), backup_dir=backups)
    text = config.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    index = next(i for i, line in enumerate(lines) if line.startswith("model = "))
    lines[index] = lines[index].rstrip("\r\n") + " # duplicate annotation\n"
    config.write_text("".join(lines) + "\n# duplicate annotation\n", encoding="utf-8")
    restore(checkpoint.manifest_path)
    assert config.is_file()
    text = config.read_text(encoding="utf-8")
    assert text.count("# duplicate annotation") == 2
    assert tomllib.loads(text) == {}


def test_later_owned_hash_in_a_string_does_not_become_a_comment(paths):
    config, catalog, helper, backups = paths
    hash_helper = helper.with_name("auth # helper.exe")
    hash_helper.write_bytes(helper.read_bytes())
    checkpoint = apply(prepare(config, catalog_path=catalog, auth_command=hash_helper, port=18765),
                       backup_dir=backups)
    # A whitespace-only later edit leaves no user comment or setting to retain.
    config.write_bytes(config.read_bytes() + b"\n")
    restore(checkpoint.manifest_path)
    assert not config.exists()


@pytest.mark.parametrize("layout", ["provider", "features", "created_proxy"])
def test_peer_partial_restore_noncontiguous_owned_tables(paths, layout):
    import re
    import tomlkit
    from tomlkit.container import OutOfOrderTableProxy

    config, _, _, backups = paths
    originals = {
        "provider": b'''[model_providers.codex_app_deepseek.auth]
command = "previous-helper.exe"
args = ["previous"]
timeout_ms = 100
refresh_interval_ms = 100
[model_providers.keep]
name = "unrelated"
[model_providers.codex_app_deepseek]
name = "Previous"
base_url = "https://previous.invalid/v1"
wire_api = "responses"
supports_websockets = false
supports_standalone_web_search = true
''',
        "features": b'''[features.child]
keep = true
[other]
keep = true
[features]
code_mode = false
code_mode_only = false
standalone_web_search = false
''',
    }
    original = originals.get(layout)
    if original is not None:
        config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    text = config.read_text(encoding="utf-8")
    expected = tomllib.loads(original.decode()) if original is not None else {}
    if layout == "created_proxy":
        pattern = r"(?ms)^\[model_providers\.codex_app_deepseek\]\r?\n.*?(?=^\[|\Z)"
        block = re.search(pattern, text).group(0)
        text = text.replace(block, "", 1)
        text += ('\n[model_providers.keep]\nname = "unrelated"\n' +
                 block + "# annotation in the removed owned fragment\n")
        expected = {"model_providers": {"keep": {"name": "unrelated"}}}
    else:
        text += "\n# independent later annotation\n"
        if layout == "features":
            text = text.replace("standalone_web_search = true",
                                "standalone_web_search = true # later boolean annotation", 1)
    parsed = tomlkit.parse(text)
    target = parsed["features"] if layout == "features" else parsed["model_providers"][PROVIDER_ID]
    assert isinstance(target, OutOfOrderTableProxy)
    config.write_text(text, encoding="utf-8")
    restore(checkpoint.manifest_path)
    restored = config.read_text(encoding="utf-8")
    assert tomllib.loads(restored) == expected
    note = "annotation in the removed owned fragment" if layout == "created_proxy" else "independent later annotation"
    assert restored.count(note) == 1
    if layout == "features":
        assert restored.count("later boolean annotation") == 1
    once = config.read_bytes()
    restore(checkpoint.manifest_path)
    assert config.read_bytes() == once


@pytest.mark.parametrize("current_array_annotation", [False, True])
def test_peer_restore_original_array_syntax_and_current_annotations(paths, current_array_annotation):
    config, _, _, backups = paths
    original = b'''[model_providers.codex_app_deepseek.auth]
command = "previous-helper.exe"
args = [
  "previous#literal", ## original B array annotation
  # repeated B annotation
  # repeated B annotation
]
'''
    config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    current = config.read_text(encoding="utf-8")
    if current_array_annotation:
        current = current.replace('args = ["auth-token"]',
            'args = [\n "auth-token", # later C array annotation\n # repeated B annotation\n]', 1)
    current += "\n# independent later annotation\n"
    config.write_text(current, encoding="utf-8")
    restore(checkpoint.manifest_path)
    restored = config.read_text(encoding="utf-8")
    assert tomllib.loads(restored) == tomllib.loads(original.decode())
    assert restored.count("## original B array annotation") == 1
    assert restored.count("# repeated B annotation") == 2
    assert restored.count("# independent later annotation") == 1
    assert '"previous#literal"' in restored
    assert restored.count("# later C array annotation") == int(current_array_annotation)
    once = config.read_bytes()
    restore(checkpoint.manifest_path)
    assert config.read_bytes() == once


@pytest.mark.parametrize("later_comment", ["# current outer annotation", ""])
def test_peer_current_outer_annotation_edits_are_not_reverted(paths, later_comment):
    config, _, _, backups = paths
    original = b'model = "previous" # original outer annotation\n'
    config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    current = config.read_text(encoding="utf-8").replace("# original outer annotation", later_comment, 1)
    config.write_text(current, encoding="utf-8")
    restore(checkpoint.manifest_path)
    restored = config.read_text(encoding="utf-8")
    assert tomllib.loads(restored) == {"model": "previous"}
    assert "# original outer annotation" not in restored
    assert restored.count("# current outer annotation") == int(bool(later_comment))
    once = config.read_bytes()
    restore(checkpoint.manifest_path)
    assert config.read_bytes() == once


def test_peer_prepare_same_auth_arguments_preserves_array_annotations(paths):
    config, _, _, backups = paths
    original = b'''[model_providers.codex_app_deepseek.auth]
args = [
 "auth-token", # original same-value annotation
]
'''
    config.write_bytes(original)
    planned = prepared(paths)
    assert "# original same-value annotation" in planned.rendered_text
    checkpoint = apply(planned, backup_dir=backups)
    assert "# original same-value annotation" in config.read_text(encoding="utf-8")
    config.write_text(config.read_text(encoding="utf-8") + "\n# later note\n", encoding="utf-8")
    restore(checkpoint.manifest_path)
    text = config.read_text(encoding="utf-8")
    assert tomllib.loads(text) == tomllib.loads(original.decode())
    assert text.count("# original same-value annotation") == 1
    assert text.count("# later note") == 1


@pytest.mark.parametrize("kind", ["scalar", "array"])
def test_peer_old_valid_restore_evidence_keeps_original_producer_quote_format(paths, kind):
    import hashlib
    config, _, _, backups = paths
    if kind == "scalar":
        original = b"model = 'previous'\n"
        restored = b'model = "previous"\n\n# later independent annotation\n'
    else:
        original = b"[model_providers.codex_app_deepseek.auth]\nargs = ['previous']\n"
        restored = (b'\n[model_providers.codex_app_deepseek]\n\n'
                    b'[model_providers.codex_app_deepseek.auth]\n'
                    b'args = ["previous"]\n\n# later independent annotation\n')
    config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    captured = config.read_bytes() + b"\n# later independent annotation\n"
    # Golden R byte layouts were independently produced by the preceding version.
    # Preserve its strict captured-input/output proof, rather than equating TOML values.
    directory = checkpoint.manifest_path.parent
    (directory / "restore-input.toml").write_bytes(captured)
    (directory / "restored.toml").write_bytes(restored)
    data = json.loads(checkpoint.manifest_path.read_text(encoding="utf-8"))
    data.update(state="restored", restore_input_file="restore-input.toml",
                restore_input_sha256=hashlib.sha256(captured).hexdigest(),
                restored_file="restored.toml",
                restored_sha256=hashlib.sha256(restored).hexdigest(),
                restore_prepared_at="2000-01-01T00:00:00+00:00")
    checkpoint.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    config.write_bytes(restored)
    manifest_before = checkpoint.manifest_path.read_bytes()
    restore(checkpoint.manifest_path)
    assert config.read_bytes() == restored
    assert checkpoint.manifest_path.read_bytes() == manifest_before


def test_peer_original_array_annotations_do_not_mask_unrelated_duplicate_comments(paths):
    config, _, _, backups = paths
    original = (b'[model_providers.codex_app_deepseek.auth]\nargs = [\n'
                b' "previous", # identical annotation\n'
                b' # identical annotation\n]\n')
    config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    config.write_bytes(config.read_bytes() +
                       b'\n[user_table]\nvalue = true\n# identical annotation\n# identical annotation\n')
    restore(checkpoint.manifest_path)
    text = config.read_text(encoding="utf-8")
    assert text.count("# identical annotation") == 4
    expected = tomllib.loads(original.decode())
    expected["user_table"] = {"value": True}
    assert tomllib.loads(text) == expected
    once = config.read_bytes()
    restore(checkpoint.manifest_path)
    assert config.read_bytes() == once


@pytest.mark.parametrize("annotation, rendered_annotation",
                         [("# header annotation", "# header annotation"),
                          ("#compact-header", "# compact-header")])
def test_peer_old_valid_header_restore_evidence_keeps_parent_table_rendering(paths, annotation, rendered_annotation):
    import hashlib
    config, _, _, backups = paths
    original = b'[model_providers.keep]\nname = "unrelated"\n'
    config.write_bytes(original)
    checkpoint = apply(prepared(paths), backup_dir=backups)
    captured = config.read_bytes().replace(b"[model_providers.codex_app_deepseek]",
        b"[model_providers.codex_app_deepseek] " + annotation.encode(), 1)
    # Exact output from the preceding deployed producer, including the parent header.
    restored = (b'\n[model_providers]\n[model_providers.keep]\nname = "unrelated"\n\n' +
                rendered_annotation.encode() + b'\n')
    directory = checkpoint.manifest_path.parent
    (directory / "restore-input.toml").write_bytes(captured)
    (directory / "restored.toml").write_bytes(restored)
    data = json.loads(checkpoint.manifest_path.read_text(encoding="utf-8"))
    data.update(state="restored", restore_input_file="restore-input.toml",
                restore_input_sha256=hashlib.sha256(captured).hexdigest(),
                restored_file="restored.toml", restored_sha256=hashlib.sha256(restored).hexdigest(),
                restore_prepared_at="2000-01-01T00:00:00+00:00")
    checkpoint.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    config.write_bytes(restored)
    manifest_before = checkpoint.manifest_path.read_bytes()
    restore(checkpoint.manifest_path)
    assert config.read_bytes() == restored
    assert checkpoint.manifest_path.read_bytes() == manifest_before
