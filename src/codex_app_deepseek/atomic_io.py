"""Standard-library-only atomic file publication for install and runtime paths."""
from pathlib import Path
import hashlib
import os
import stat
import tempfile
import time


class AtomicPublicationConflict(ValueError):
    """The original file identity or content changed before publication."""


CAPTURE_CURRENT_TARGET = object()


def target_state(path: Path):
    """Read an identity-bound snapshot; no content escapes the local digest."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None

    def identity(info):
        # Windows lstat/fstat can expose different legacy st_ctime meanings.
        # Stable identity, write time and content still bind every CAS attempt.
        timestamp = getattr(info, "st_birthtime_ns", None) if os.name == "nt" else info.st_ctime_ns
        return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_size, info.st_mtime_ns, timestamp,
                getattr(info, "st_file_attributes", 0))

    if (not stat.S_ISREG(before.st_mode)
            or getattr(before, "st_file_attributes", 0) & 0x400):
        raise AtomicPublicationConflict("Atomic publication target is not a regular file")
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        content = source.read()
        finished = os.fstat(source.fileno())
    try:
        after = path.lstat()
    except FileNotFoundError as exc:
        raise AtomicPublicationConflict("Atomic publication target changed while reading") from exc
    if not identity(before) == identity(opened) == identity(finished) == identity(after):
        raise AtomicPublicationConflict("Atomic publication target changed while reading")
    return (identity(after), hashlib.sha256(content).hexdigest())


def atomic_write(path: Path, data: bytes, *,
                  expected_sha256=CAPTURE_CURRENT_TARGET,
                  retry_transient: bool = True,
                  prepare_temp=None) -> None:
    # One bounded publisher serves config, receipts and metadata. Callers must
    # never add another retry loop around the whole operation.
    path = Path(path)
    captured = target_state(path)
    if expected_sha256 is not CAPTURE_CURRENT_TARGET:
        digest = captured[1] if captured is not None else None
        if digest != expected_sha256:
            raise AtomicPublicationConflict("Config changed before atomic publication; nothing published")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".app-deepseek-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.close(descriptor)
        if prepare_temp is not None:
            prepare_temp(temporary)
        with temporary.open("wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        for attempt in range(8 if retry_transient else 1):
            if target_state(path) != captured:
                raise AtomicPublicationConflict("Atomic publication target changed; nothing published")
            try:
                os.replace(temporary, path)
                return
            except PermissionError as exc:
                if (not retry_transient or os.name != "nt"
                        or getattr(exc, "winerror", None) not in {5, 32, 33}
                        or attempt == 7):
                    raise
                time.sleep(min(0.01 * (2 ** attempt), 0.2))
    finally:
        temporary.unlink(missing_ok=True)

