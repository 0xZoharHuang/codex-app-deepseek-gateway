"""Standard-library path redirect checks across supported Python versions."""
import os
import stat


def _is_link_or_reparse(path):
    """Reject redirects without relying on junction APIs introduced in Python 3.12."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))


def _sqlite_filename(path, *, read_only=False):
    """Format SQLite's filename without changing the caller's logical Path.

    SQLite's Windows VFS needs extended spelling for the database and its longer
    journal sidecars. Ordinary names are made absolute lexically; existing
    extended names remain intact. Callers retain all path guards and uri=True
    for the escaped read-only form.
    """
    value = os.fspath(path)
    if os.name == "nt":
        if not value.startswith("\\\\?\\"):
            value = os.path.abspath(value)
            value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
        if read_only:
            from urllib.parse import quote
            return "file:" + quote(value, safe="") + "?mode=ro"
    elif read_only:
        from pathlib import Path
        return Path(value).absolute().as_uri() + "?mode=ro"
    return value
