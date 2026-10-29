"""Explicit, reversible Codex App provider configuration. No credential access."""
from __future__ import annotations
from .paths import _is_link_or_reparse

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import stat
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

import tomlkit

PROVIDER_ID = "codex_app_deepseek"
PINNED_MODEL = "deepseek/deepseek-v4.1-flash-1017"


class ConfigurationError(ValueError):
    """Configuration cannot safely be prepared or changed."""


class ConfigurationConflict(ConfigurationError):
    """The file changed or contains conflicting provider authentication."""


@dataclass(frozen=True)
class PreparedConfig:
    config_path: Path
    catalog_path: Path
    catalog_sha256: str
    before_sha256: str | None
    after_sha256: str
    was_present: bool
    changed_settings: tuple[str, ...]
    rendered_text: str = field(repr=False)
    _original: bytes | None = field(repr=False)

    @property
    def changed(self) -> bool:
        return self.before_sha256 != self.after_sha256


@dataclass(frozen=True)
class ConfigCheckpoint:
    manifest_path: Path
    config_path: Path
    before_sha256: str | None
    applied_sha256: str
    was_present: bool
    changed: bool
    changed_settings: tuple[str, ...]


@dataclass(frozen=True)
class RestoreResult:
    manifest_path: Path
    config_path: Path
    restored_sha256: str | None
    removed_created_config: bool


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _regular_path(path: str | Path, label: str) -> Path:
    result = Path(path).absolute()
    # Refuse links/junctions: caller must explicitly choose the real destination.
    for part in [result, *result.parents]:
        if _is_link_or_reparse(part):
            raise ConfigurationError(f"{label} must not traverse a symlink or junction")
    if result.exists() and not result.is_file():
        raise ConfigurationError(f"{label} must be a regular file")
    return result


def _read_optional(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def prepare(config_path: str | Path, *, catalog_path: str | Path,
            auth_command: str | Path, port: int, model: str = PINNED_MODEL,
            auth_args: list[str] | tuple[str, ...] | None = None) -> PreparedConfig:
    """Render a merge in memory, without writing files or launching any process.

    Catalog validation is intentionally structural; it is not a native-version
    compatibility verdict. The caller supplies the version-matched catalog.
    """
    arguments = ["auth-token"] if auth_args is None else auth_args
    if not isinstance(arguments, (list, tuple)) or any(not isinstance(arg, str) or "\x00" in arg for arg in arguments):
        raise ConfigurationError("auth_args must be an array of strings without NUL characters")
    arguments = list(arguments)
    config = _regular_path(config_path, "config_path")
    if config.suffix.lower() != ".toml":
        raise ConfigurationError("config_path must be a TOML file; authentication stores are not supported")
    catalog = _regular_path(catalog_path, "catalog_path")
    command = _regular_path(auth_command, "auth_command")
    if not Path(auth_command).is_absolute() or not command.is_file():
        raise ConfigurationError("auth_command must be an existing absolute executable path")
    if config in (catalog, command):
        raise ConfigurationError("Config, catalog, and auth executable must be distinct files")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ConfigurationError("port must be an integer from 1 through 65535")
    if model != PINNED_MODEL:
        raise ConfigurationError("Only the pinned DeepSeek model is supported")
    try:
        catalog_bytes = catalog.read_bytes()
        model_catalog = json.loads(catalog_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ConfigurationError("catalog_path must contain readable JSON") from exc
    if (not isinstance(model_catalog, dict) or not isinstance(model_catalog.get("models"), list)
            or not any(isinstance(entry, dict) and entry.get("slug") == model
                       for entry in model_catalog["models"])):
        raise ConfigurationError("Catalog must contain a models array with the pinned model slug")
    original = _read_optional(config)
    try:
        text = "" if original is None else original.decode("utf-8-sig")
        before_settings = tomllib.loads(text)
        document = tomlkit.parse(text)
    except (UnicodeError, ValueError) as exc:
        raise ConfigurationError("Existing config must be valid UTF-8 TOML") from exc
    try:
        providers = document.get("model_providers")
        if providers is None:
            providers = tomlkit.table()
            document["model_providers"] = providers
        if not isinstance(providers, dict):
            raise ConfigurationError("model_providers must be a TOML table")
        provider = providers.get(PROVIDER_ID)
        if provider is None:
            provider = tomlkit.table()
            providers[PROVIDER_ID] = provider
        if not isinstance(provider, dict):
            raise ConfigurationError("Target provider must be a TOML table")
        if any(key in provider for key in ("env_key", "experimental_bearer_token", "requires_openai_auth")):
            raise ConfigurationConflict("Target provider already has incompatible authentication fields")
        document["model_provider"] = PROVIDER_ID
        document["model"] = model
        # Exa provides live search, not OpenAI's private cached index.
        document["web_search"] = "live"
        document["model_catalog_json"] = str(catalog)
        memories = document.get("memories")
        if memories is None:
            memories = tomlkit.table()
            document["memories"] = memories
        if not isinstance(memories, dict):
            raise ConfigurationError("memories must be a TOML table")
        memories["extract_model"] = model
        memories["consolidation_model"] = model
        if original is None:
            document["sandbox_mode"] = "workspace-write"
            document["approval_policy"] = "on-request"
        provider["name"] = "Codex App DeepSeek Gateway"
        provider["base_url"] = f"http://127.0.0.1:{port}/v1"
        provider["wire_api"] = "responses"
        provider["supports_websockets"] = False
        provider["supports_standalone_web_search"] = True
        features = document.get("features")
        if features is None:
            features = tomlkit.table()
            document["features"] = features
        if not isinstance(features, dict):
            raise ConfigurationError("features must be a TOML table")
        features["standalone_web_search"] = True
        # Preserve this App's existing CodeModeOnly default through explicit
        # native features, while leaving any user-selected mode untouched.
        default_tool_mode = not any(key in features for key in ("code_mode", "code_mode_only"))
        if default_tool_mode:
            features["code_mode"] = True
            features["code_mode_only"] = True
        auth = provider.get("auth")
        if auth is None:
            auth = tomlkit.table()
        if not isinstance(auth, dict):
            raise ConfigurationError("Provider auth must be a TOML table")
        auth["command"] = str(command)
        if not _equal(_at(before_settings, ("model_providers", PROVIDER_ID, "auth", "args")), arguments):
            auth["args"] = arguments
        auth["timeout_ms"] = 30000
        auth["refresh_interval_ms"] = 300000
        provider["auth"] = auth
        rendered = tomlkit.dumps(document)
        if "\r\n" in text:
            rendered = rendered.replace("\r\n", "\n").replace("\n", "\r\n")
        if original is not None and original.startswith(b"\xef\xbb\xbf"):
            rendered = "\ufeff" + rendered
        parsed = tomllib.loads(rendered.lstrip("\ufeff"))
        assert parsed["model_provider"] == PROVIDER_ID
        assert parsed["model_catalog_json"] == str(catalog)
        managed_paths = ["model_provider", "model", "model_catalog_json", "web_search", "features.standalone_web_search", "memories.extract_model",
                         "memories.consolidation_model", *(
                             f"model_providers.{PROVIDER_ID}.{key}"
                             for key in ("name", "base_url", "wire_api", "supports_websockets", "supports_standalone_web_search", "auth"))]
        if default_tool_mode:
            managed_paths += ["features.code_mode", "features.code_mode_only"]
        if original is None:
            managed_paths += ["sandbox_mode", "approval_policy"]
        missing = object()
        def setting(settings: dict, dotted_path: str):
            value = settings
            for key in dotted_path.split("."):
                if not isinstance(value, dict) or key not in value:
                    return missing
                value = value[key]
            return value
        changed_settings = tuple(path for path in managed_paths
                                 if not _equal(setting(before_settings, path), setting(parsed, path)))
    except ConfigurationError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise ConfigurationError("Provider configuration cannot be merged safely") from exc
    return PreparedConfig(config, catalog, _digest(catalog_bytes), _digest(original) if original is not None else None,
                          _digest(rendered.encode("utf-8")), original is not None, changed_settings, rendered, original)


def _private(path: Path, *, directory: bool = False) -> None:
    """Set owner-only permissions before sensitive backup content is written."""
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)
        return
    system32 = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
    try:
        output = subprocess.run([str(system32 / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
                                check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        sid = next(csv.reader(io.StringIO(output.stdout.strip())))[1]
        if not sid.startswith("S-1-") or any(c not in "S-0123456789" for c in sid):
            raise ConfigurationError("Cannot resolve current Windows security identifier")
        # /grant:r replaces grants for named trustees only; it can leave unrelated
        # explicit ACEs behind. Install a complete protected DACL atomically instead.
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        api.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
        api.GetSecurityDescriptorDacl.restype = wintypes.BOOL
        api.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        api.SetNamedSecurityInfoW.restype = wintypes.DWORD
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        descriptor = ctypes.c_void_p()
        flags = "OICI" if directory else ""
        sddl = f"D:P(A;{flags};FA;;;{sid})(A;{flags};FA;;;SY)"
        if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise OSError("Cannot create private security descriptor")
        try:
            dacl = ctypes.c_void_p()
            present, defaulted = wintypes.BOOL(), wintypes.BOOL()
            if not api.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)) or not present:
                raise OSError("Cannot resolve private DACL")
            if api.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, dacl, None) != 0:
                raise OSError("Cannot install private DACL")
        finally:
            kernel.LocalFree(descriptor)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
        raise ConfigurationError("Cannot create a private configuration backup") from exc


from . import atomic_io as _atomic_io

_CAPTURE_CURRENT_TARGET = _atomic_io.CAPTURE_CURRENT_TARGET


def _atomic_target_state(path: Path):
    try:
        return _atomic_io.target_state(path)
    except _atomic_io.AtomicPublicationConflict as exc:
        raise ConfigurationConflict(str(exc)) from exc


def _atomic_write(path: Path, data: bytes, *,
                  expected_sha256=_CAPTURE_CURRENT_TARGET,
                  retry_transient: bool = True,
                  private_permissions: bool = True) -> None:
    try:
        return _atomic_io.atomic_write(
            path, data, expected_sha256=expected_sha256,
            retry_transient=retry_transient,
            prepare_temp=_private if private_permissions else None)
    except _atomic_io.AtomicPublicationConflict as exc:
        raise ConfigurationConflict(str(exc)) from exc


def _write_manifest(path: Path, manifest: dict) -> None:
    _atomic_write(path, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _matches(path: Path, expected: str | None) -> bool:
    current = _read_optional(path)
    return (_digest(current) if current is not None else None) == expected


def apply(prepared: PreparedConfig, *, backup_dir: str | Path) -> ConfigCheckpoint:
    """Apply exactly a prepared merge, keeping a private rollback checkpoint.

    Stop the relevant App before applying. The optimistic hash check protects
    edits made since prepare; it cannot lock out an unrelated concurrent editor.
    """
    config = _regular_path(prepared.config_path, "config_path")
    if config.suffix.lower() != ".toml":
        raise ConfigurationError("config_path must be a TOML file")
    if not _matches(prepared.catalog_path, prepared.catalog_sha256):
        raise ConfigurationConflict("Catalog changed after prepare; nothing applied")
    if not _matches(config, prepared.before_sha256):
        raise ConfigurationConflict("Config changed after prepare; nothing applied")
    original_hash = _digest(prepared._original) if prepared._original is not None else None
    if original_hash != prepared.before_sha256 or prepared.was_present != (prepared._original is not None):
        raise ConfigurationError("Prepared original state does not match its hash and existence flag")
    if _digest(prepared.rendered_text.encode("utf-8")) != prepared.after_sha256:
        raise ConfigurationError("Prepared configuration hash does not match content")
    root = Path(backup_dir).absolute()
    for part in [root, *root.parents]:
        if _is_link_or_reparse(part):
            raise ConfigurationError("backup_dir must not traverse a symlink or junction")
    root.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = root / uuid4().hex
    checkpoint_dir.mkdir(mode=0o700)
    _private(checkpoint_dir, directory=True)
    backup = checkpoint_dir / "original.toml"
    if prepared._original is not None:
        _atomic_write(backup, prepared._original)
    manifest_path = checkpoint_dir / "checkpoint.json"
    _atomic_write(checkpoint_dir / "applied.toml", prepared.rendered_text.encode("utf-8"))
    manifest = {"schema_version": 2, "applied_file": "applied.toml", "config_path": str(config), "was_present": prepared.was_present,
                "before_sha256": prepared.before_sha256, "applied_sha256": prepared.after_sha256,
                "backup_file": "original.toml" if prepared.was_present else None,
                "changed_settings": list(prepared.changed_settings),
                "created_at": datetime.now(timezone.utc).isoformat(), "state": "prepared"}
    _write_manifest(manifest_path, manifest)
    config.parent.mkdir(parents=True, exist_ok=True)
    if not _matches(config, prepared.before_sha256):
        raise ConfigurationConflict("Config changed while backing up; nothing applied")
    if prepared.changed:
        _atomic_write(config, prepared.rendered_text.encode("utf-8"),
                      expected_sha256=prepared.before_sha256, retry_transient=True)
    manifest["state"] = "applied"
    _write_manifest(manifest_path, manifest)
    return ConfigCheckpoint(manifest_path, config, prepared.before_sha256, prepared.after_sha256,
                            prepared.was_present, prepared.changed, prepared.changed_settings)


_MISSING = object()
_MANAGED_GROUPS = {
    "model_provider", "model", "model_catalog_json", "web_search",
    "sandbox_mode", "approval_policy", "features.standalone_web_search",
    "features.code_mode", "features.code_mode_only",
    "memories.extract_model", "memories.consolidation_model",
    "plugins.codex-app-tools@openai-bundled.mcp_servers.codex_app.enabled",
    *(f"model_providers.{PROVIDER_ID}.{key}" for key in
      ("name", "base_url", "wire_api", "supports_websockets", "supports_standalone_web_search", "auth")),
}
_MANAGED_LEAVES = (_MANAGED_GROUPS - {f"model_providers.{PROVIDER_ID}.auth"}) | {
    f"model_providers.{PROVIDER_ID}.auth.{key}" for key in
    ("command", "args", "timeout_ms", "refresh_interval_ms")
}


def _at(document, parts):
    value = document
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, float):
        if math.isnan(left) or math.isnan(right):
            return math.isnan(left) and math.isnan(right)
        if left == right == 0.0:
            return math.copysign(1.0, left) == math.copysign(1.0, right)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    return left == right


def _diff_leaves(before, after, prefix=()):
    if isinstance(before, dict) or isinstance(after, dict):
        if before is not _MISSING and not isinstance(before, dict):
            raise ConfigurationError("Checkpoint changed a configuration table's shape")
        if after is not _MISSING and not isinstance(after, dict):
            raise ConfigurationError("Checkpoint changed a configuration table's shape")
        left = {} if before is _MISSING else before
        right = {} if after is _MISSING else after
        if (before is _MISSING and not right) or (after is _MISSING and not left):
            # Empty tables have semantic presence, even though there is no leaf.
            # Such snapshot-only changes must not bypass ownership validation.
            return [prefix]
        result = []
        for key in sorted(set(left) | set(right)):
            result.extend(_diff_leaves(left.get(key, _MISSING), right.get(key, _MISSING), (*prefix, key)))
        return result
    return [prefix] if not _equal(before, after) else []


def _created_tables(before, after, path=()):
    result = set()
    if isinstance(after, dict):
        if before is _MISSING:
            result.add(path)
        for key, value in after.items():
            prior = before.get(key, _MISSING) if isinstance(before, dict) else _MISSING
            result.update(_created_tables(prior, value, (*path, key)))
    return result


def _item_comments(value, *, include_outer=True):
    """Read actual TOML trivia, including comments inside arrays and inline tables."""
    from tomlkit.container import Container
    from tomlkit.items import AoT, Array, InlineTable, Item, Table, Whitespace

    if isinstance(value, Whitespace):
        return
    if isinstance(value, Item):
        # Implicit parents share the explicit child's header trivia.
        implicit = isinstance(value, Table) and value.is_super_table()
        comment = getattr(value.trivia, "comment", "")
        if comment and not implicit and include_outer:
            yield comment
        if isinstance(value, Array):
            # This is the same token iterator used by the pinned tomlkit renderer.
            for child in value._iter_items():
                yield from _item_comments(child)
        elif isinstance(value, AoT):
            for child in value.body:
                yield from _item_comments(child)
        elif isinstance(value, (Table, InlineTable)):
            yield from _item_comments(value.value)
    elif isinstance(value, Container):
        for _, child in value.body:
            yield from _item_comments(child)


def _comment_identity(comment):
    # Header rendering historically normalizes spacing after the first '#'.
    # Subsequent '#' characters and duplicate annotations remain significant.
    return comment[1:].strip() if comment.startswith("#") else comment


def _retain_removed_comments(table, comments, replacement):
    """Keep lost current annotations as standalone document-footer comments."""
    from collections import Counter
    from tomlkit.items import Comment, Trivia

    retained = Counter(_comment_identity(item) for item in _item_comments(replacement))
    for comment in comments:
        key = _comment_identity(comment)
        if retained[key]:
            retained[key] -= 1
        else:
            # Keep the complete comment, including additional '#' characters and duplicates.
            table.add(Comment(Trivia(comment=comment, trail="\n")))


def _syntax_at(document, parts):
    """Read syntax items without the proxy mapping's boolean unwrapping."""
    from tomlkit.container import OutOfOrderTableProxy

    for part in parts:
        if isinstance(document, OutOfOrderTableProxy):
            # The pinned renderer uses this container to retain raw item trivia.
            document = document._internal_container
        document = document.item(part)
    return document


def _prune_created_tables(document, created_tables, path=()):
    from copy import deepcopy
    from tomlkit.container import Container
    from tomlkit.items import Comment, InlineTable, Table, Trivia

    for key in list(document):
        value = document[key]
        if not isinstance(value, dict):
            continue
        _prune_created_tables(value, created_tables, (*path, key))
        if not value and (*path, key) in created_tables:
            comments = []
            header_comment = ""
            if isinstance(value, (Table, InlineTable)):
                comments = [deepcopy(item) for _, item in value.value.body if isinstance(item, Comment)]
                header_comment = ("" if isinstance(value, Table) and value.is_super_table()
                                  else value.trivia.comment)
            del document[key]
            # Preserve the preceding renderer's normal Table layout and its
            # strict C/R byte proofs. Proxy/inline destinations use the footer
            # conservation pass instead of unsupported comment insertion.
            if isinstance(document, (Container, Table)):
                if header_comment:
                    rendered = tomlkit.comment(header_comment.lstrip("#").strip())
                    if _comment_identity(header_comment) != _comment_identity(rendered.trivia.comment):
                        rendered = Comment(Trivia(comment=header_comment, trail="\n"))
                    document.add(rendered)
                for comment in comments:
                    document.add(comment)


def _three_way_bytes(original, applied, current, declared):
    """Restore owned leaves only; unrelated content stays in the current TOML tree."""
    if not isinstance(declared, list) or len(set(declared)) != len(declared) or any(
        not isinstance(key, str) or key not in _MANAGED_GROUPS for key in declared
    ):
        raise ConfigurationError("Invalid checkpoint owned settings")
    before = tomllib.loads(original.decode("utf-8-sig")) if original is not None else {}
    after = tomllib.loads(applied.decode("utf-8-sig"))
    now = tomllib.loads(current.decode("utf-8-sig")) if current is not None else {}
    changed = _diff_leaves(before, after)
    for parts in changed:
        dotted = ".".join(parts)
        if parts not in {tuple(item.split(".")) for item in _MANAGED_LEAVES} or not any(
            parts[:len(item.split("."))] == tuple(item.split(".")) for item in declared):
            raise ConfigurationError("Checkpoint snapshots contain an unowned change")
    actual_groups = {item for item in declared if any(
        ".".join(parts) == item or ".".join(parts).startswith(item + ".") for parts in changed)}
    if actual_groups != set(declared):
        raise ConfigurationError("Checkpoint owned settings do not match its snapshots")
    # Validate every path before changing even an in-memory document. A changed
    # parent shape is a conflict, not an absent leaf that can be recreated.
    pending = []
    for parts in changed:
        for count in range(1, len(parts)):
            parent = _at(now, parts[:count])
            if parent is not _MISSING and not isinstance(parent, dict):
                raise ConfigurationConflict("An owned setting's parent table was changed; refusing later edits")
        b, a, c = (_at(item, parts) for item in (before, after, now))
        if _equal(c, b):
            continue
        if not _equal(c, a):
            raise ConfigurationConflict("An owned setting was changed; refusing to overwrite later edits")
        pending.append((parts, b))
    if current == applied:
        return original
    if current is None:
        # File existence is part of the original state, including empty/comment-only TOML.
        if original is not None or pending:
            raise ConfigurationConflict("Config was removed after apply; refusing later edits")
        return None
    text = current.decode("utf-8-sig")
    document = tomlkit.parse(text)
    current_comments = list(_item_comments(document))
    original_document = tomlkit.parse(original.decode("utf-8-sig")) if original is not None else None
    original_comments = []
    from collections import Counter
    for parts, prior in pending:
        table = document
        for part in parts[:-1]:
            if part not in table:
                table[part] = tomlkit.table()
            table = table[part]
        if prior is _MISSING:
            table.pop(parts[-1], None)
        else:
            current_item = _syntax_at(table, (parts[-1],)) if parts[-1] in table else None
            retained = Counter(_comment_identity(item) for item in _item_comments(current_item, include_outer=False))
            for comment in _item_comments(_syntax_at(original_document, parts), include_outer=False):
                key = _comment_identity(comment)
                if retained[key]:
                    retained[key] -= 1
                else:
                    original_comments.append(comment)
            # Keep the established value renderer so valid older C/R byte proofs
            # remain reproducible; recover B's internal annotations separately.
            table[parts[-1]] = prior
    _prune_created_tables(document, _created_tables(before, after))
    # Native proxy deletion may remove empty physical fragments. Retain every
    # current comment token that disappeared anywhere, including its multiplicity.
    _retain_removed_comments(document, current_comments, document)
    # Add B annotations only after conserving C, so equal unrelated C comments
    # cannot mask a missing annotation from the restored owned value.
    _retain_removed_comments(document, original_comments, None)
    rendered = tomlkit.dumps(document)
    if original is None and not rendered.strip():
        return None
    if "\r\n" in text:
        rendered = rendered.replace("\r\n", "\n").replace("\n", "\r\n")
    if current.startswith(b"\xef\xbb\xbf"):
        rendered = "\ufeff" + rendered
    tomllib.loads(rendered.lstrip("\ufeff"))
    return rendered.encode("utf-8")


def _restore_evidence(path: Path, manifest: dict, original: bytes | None, applied: bytes | None):
    """Verify the exact captured input C and recomputable three-way output R."""
    evidence_fields = {"restore_input_file", "restore_input_sha256", "restored_file", "restore_prepared_at"}
    if not any(name in manifest for name in evidence_fields):
        return None
    if not evidence_fields <= set(manifest) or "restored_sha256" not in manifest:
        raise ConfigurationError("Incomplete restore preparation evidence")
    if not isinstance(manifest["restore_prepared_at"], str) or not manifest["restore_prepared_at"]:
        raise ConfigurationError("Invalid restore preparation timestamp")
    def snapshot(name, hash_name, expected_file):
        file, expected = manifest[name], manifest[hash_name]
        if file is None:
            if expected is not None:
                raise ConfigurationError("Absent restore snapshot has a hash")
            return None
        if file != expected_file or not isinstance(expected, str) or len(expected) != 64:
            raise ConfigurationError("Invalid restore snapshot metadata")
        value = _regular_path(path.parent / expected_file, "restore snapshot").read_bytes()
        if _digest(value) != expected:
            raise ConfigurationError("Restore snapshot hash mismatch")
        return value
    captured = snapshot("restore_input_file", "restore_input_sha256", "restore-input.toml")
    restored = snapshot("restored_file", "restored_sha256", "restored.toml")
    if manifest["schema_version"] == 2:
        if applied is None:
            raise ConfigurationError("Restore evidence requires the applied snapshot")
        recomputed = _three_way_bytes(original, applied, captured, manifest["changed_settings"])
    else:
        if (_digest(captured) if captured is not None else None) != manifest["applied_sha256"]:
            raise ConfigurationError("Legacy restore input does not match applied hash")
        recomputed = original
    if recomputed != restored:
        raise ConfigurationError("Restore evidence does not reproduce the owned three-way transformation")
    return captured, restored


def restore(manifest_path: str | Path) -> RestoreResult:
    """Restore owned settings with private input/output evidence and crash recovery."""
    path = _regular_path(manifest_path, "manifest_path")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (type(manifest["schema_version"]) is not int or manifest["schema_version"] not in {1, 2}
                or manifest["state"] not in {"prepared", "applied", "restore_prepared", "restored"}):
            raise ConfigurationError("Unsupported checkpoint")
        config = _regular_path(manifest["config_path"], "checkpoint config_path")
        if config.suffix.lower() != ".toml":
            raise ConfigurationError("Checkpoint target must be a TOML config file")
        present = manifest["was_present"]
        if not isinstance(present, bool):
            raise ConfigurationError("Invalid checkpoint existence flag")
        expected, before = manifest["applied_sha256"], manifest["before_sha256"]
        if not isinstance(expected, str) or len(expected) != 64:
            raise ConfigurationError("Invalid checkpoint applied hash")
        if present:
            if manifest["backup_file"] != "original.toml":
                raise ConfigurationError("Invalid checkpoint backup location")
            original = _regular_path(path.parent / "original.toml", "backup file").read_bytes()
            if _digest(original) != before:
                raise ConfigurationError("Backup hash mismatch; no changes made")
        else:
            original = None
            if before is not None or manifest["backup_file"] is not None:
                raise ConfigurationError("Invalid checkpoint for previously absent config")
        applied = None
        if manifest["schema_version"] == 2:
            if manifest.get("applied_file") != "applied.toml":
                raise ConfigurationError("Invalid checkpoint applied snapshot location")
            applied = _regular_path(path.parent / "applied.toml", "applied snapshot").read_bytes()
            if _digest(applied) != expected:
                raise ConfigurationError("Applied snapshot hash mismatch; no changes made")
            # Validate scope even on an already-restored or legacy-evidence path.
            # Hashes alone cannot establish that B -> A changed only owned settings.
            _three_way_bytes(original, applied, applied, manifest["changed_settings"])
        evidence = _restore_evidence(path, manifest, original, applied)
        if manifest["state"] == "restored":
            restored_hash = manifest.get("restored_sha256", before)
            if not _matches(config, restored_hash):
                raise ConfigurationConflict("Config changed after restore; refusing to overwrite")
            if evidence is None:
                # Older checkpoints predate captured C/R snapshots. A matching
                # self-reported hash is insufficient: the owned transformation
                # must already be at a fixed point. This preserves legitimate
                # partial restores without inventing missing historical evidence.
                current = _read_optional(config)
                verified = (_three_way_bytes(original, applied, current, manifest["changed_settings"])
                            if manifest["schema_version"] == 2 else original)
                if verified != current:
                    raise ConfigurationError("Legacy restored checkpoint lacks evidence of completed restoration")
            return RestoreResult(path, config, restored_hash, restored_hash is None)
        current = _read_optional(config)
        current_hash = _digest(current) if current is not None else None
        if manifest["state"] == "restore_prepared":
            if evidence is None:
                raise ConfigurationError("Prepared restore has no verified input/output snapshots")
            captured, restored = evidence
            captured_hash = _digest(captured) if captured is not None else None
            target_hash = _digest(restored) if restored is not None else None
            if current_hash not in {captured_hash, target_hash}:
                raise ConfigurationConflict("Config changed after restore preparation; refusing later edits")
        else:
            if evidence is not None:
                raise ConfigurationError("Restore evidence appears in an incompatible checkpoint state")
            if manifest["schema_version"] == 2:
                restored = _three_way_bytes(original, applied, current, manifest["changed_settings"])
            else:
                if current_hash != expected:
                    raise ConfigurationConflict("Config no longer matches the applied hash; refusing to overwrite later edits")
                restored = original
            if not _matches(config, current_hash):
                raise ConfigurationConflict("Config changed during restore; nothing applied")
            # Both private snapshots are durable before the restore-prepared marker.
            # This records all unrelated content, not merely the owned-field result.
            if current is not None:
                _atomic_write(path.parent / "restore-input.toml", current)
            if restored is not None:
                _atomic_write(path.parent / "restored.toml", restored)
            manifest.update(state="restore_prepared",
                restore_input_file="restore-input.toml" if current is not None else None,
                restore_input_sha256=current_hash,
                restored_file="restored.toml" if restored is not None else None,
                restored_sha256=_digest(restored) if restored is not None else None,
                restore_prepared_at=datetime.now(timezone.utc).isoformat())
            _write_manifest(path, manifest)
        if not _matches(config, current_hash):
            raise ConfigurationConflict("Config changed during restore; nothing applied")
        if restored is None:
            config.unlink(missing_ok=True)
        elif restored != current:
            _atomic_write(config, restored,
                          expected_sha256=current_hash, retry_transient=True)
        restored_hash = _digest(restored) if restored is not None else None
        manifest["state"] = "restored"
        manifest["restored_sha256"] = restored_hash
        manifest["restored_at"] = datetime.now(timezone.utc).isoformat()
        _write_manifest(path, manifest)
        return RestoreResult(path, config, restored_hash, restored is None)
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise ConfigurationError("Cannot safely read or restore checkpoint") from exc
