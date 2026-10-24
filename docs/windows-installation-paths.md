# Windows installation path and packaged runtime observation

The installer leaves machine-wide LongPaths policy unchanged. It validates only
its own immutable installation layout, not the user's coding workspace paths.

Before creating a release directory or launching venv/pip, it reads the
hash-verified wheel names and predicts their normal Windows destinations,
including generated metadata and entry-point launchers. Permanent paths beyond
259 UTF-16 code units are rejected with an explicit request for a shorter install
root. Reserved device names, traversal, backslashes in archive paths, collisions,
and links are rejected. Standard purelib/platlib, data, and scripts schemes are
mapped; unknown schemes, including unqualified header layouts, fail explicitly.

This distinction is measured: the original test's permanent direct_url.json path
was 253 characters, but pip's random temporary filename made it 265. The
supported original path now completes real installation and PowerShell uninstall.
A still-deeper fixture installed through extended paths but returned
metadata.version=None from a normal Python entry point. It is now rejected
before any installation side effect; that failure evidence remains retained.

For supported roots, the installer uses the standard-library venv --without-pip
step and a separate isolated ensurepip step. Its Python child executable paths
use the Win32 extended prefix, which CPython propagates to sys.prefix for
temporary metadata writes. Stored installation paths and scheduler actions keep
their ordinary spelling. PowerShell executables do not receive that prefix:
Windows PowerShell 5.1 was observed to fail initialization when launched that way.

These checks follow the [Windows path contract](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation)
and [wheel destination schemes](https://packaging.python.org/en/latest/specifications/binary-distribution-format/).
They do not replace pip's installer or change Windows registry settings.

App observation no longer executes codex.exe from WindowsApps, because the
packaged executable returned WinError 5 even for --version in an empty dedicated
CODEX_HOME. It reads the current-user OpenAI.Codex registration, verifies the
expected Store publisher identity and Ok status, hashes the actual packaged
binary, and resolves only known verified hashes to their recorded versions.

The returned native_version_source is hash-verified-reference. An unknown
binary hash has no inferred native version and requires compatibility
revalidation. It is never filled from the requested release baseline.
The separately loaded private runtime must still be verified using the launcher
manifest and actual process executable hash.

Evidence:
- .evidence/lifecycle-long-path-pip-repro.json
- .evidence/lifecycle-long-path-prefix-experiment.json
- .evidence/lifecycle-long-path-second-boundaries.json
- .evidence/lifecycle-long-path-fix-tests-v3.json
- .evidence/lifecycle-path-and-observer-tests-v1.json
- .evidence/packaged-native-hash-observation-v2.json
