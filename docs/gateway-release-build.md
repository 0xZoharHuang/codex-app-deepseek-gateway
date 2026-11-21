# Reproduce the Python gateway bundle

This entrypoint builds a Windows x64 / Python 3.13 bundle. It does not install,
activate, configure, launch, qualify or publish an App. App acceptance and the
native runtime build remain separate gates.

The public entrypoint is `scripts/build_gateway_release.py`; the complete input
list is `release/dependencies.windows-py313.json`. Both must belong to the clean
source commit that is being built. No private QA path is needed.

## Inputs and provenance

The lock lists every dependency wheel by exact filename, distribution, version,
SHA256 and source. PyPI inputs carry the official project JSON URL and exact file
URL; the recorded hashes were checked against those official records. The four
build tools are pip 25.0.1, setuptools 84.2.0, wheel 0.48.0 and packaging 26.3.
Supply their exact wheel files too: the controller checks the selected build
Python's installed tool files against those wheel contents, including vendored
code, rather than trusting version labels alone.

### P169 companion inputs

The P169 lock requires the exact `codex-deepseek-runtime==0.7.9+cadsg.2` and
`aiohttp==3.14.5+cds.1` wheels. Standard public indexes and CDS releases do
not supply those pinned bytes. The aiohttp backport uses the
[official 3.14.5 source](https://pypi.org/project/aiohttp/3.14.5/) and
[upstream patch #14370](https://github.com/aio-libs/aiohttp/pull/14370).
Achieving the locked wheel SHA also requires the historical wheel layout.
The CDS input is a build-source reconstruction that reproduces the locked
wheel. The original Git object recorded by the lock remains unavailable.

If you have the verified qualification attachment
`cadsg-p169-dependency-sources-exact.tar.xz`, check its SHA-256
`efd54c3dd836905c5c7a41e8b6d25f4aff4862c74250d2a2c0be971ff5865394`
before extracting it to a new directory. Obtain it from the same verified
release source when the attachment is available. Inside its extracted
`cadsg-p169-dependency-sources` directory, run this on Windows x64 with
independent CPython 3.13 and Git:

~~~powershell
& $basePython -I -B .\replay.py --scratch $newReplayDirectory --git $gitExe
if ($LASTEXITCODE -ne 0) { throw 'Locked dependency replay failed' }
~~~

Use a new scratch directory outside installed environments. The offline
runner verifies the bundled build tools, reconstructs both wheels and checks
them against the committed lock. The outputs are in `cds-wheel` and
`aiohttp-locked-wheel` under the scratch directory. Use them as additional
`--dependency-wheelhouse` inputs below. The ordinary installer consumes
the resulting exact wheel files in the gateway bundle. Public binary
delivery still requires publicly obtainable pinned dependencies or the
verified replay attachment, then a clean consumer install and real App
verification for the published combination.

Obtain the declared wheel inputs before the offline build. The builder never
downloads, resolves versions, falls back to another source or modifies the build
Python. Verify input hashes against the committed lock. Preparing a new builder
venv is an explicit separate operation, for example:

~~~powershell
py -3.13 -m venv C:\CADSGBuilder
$lock = Get-Content .\release\dependencies.windows-py313.json -Raw | ConvertFrom-Json
$lock.build_tools | ForEach-Object {
  "$($_.distribution)==$($_.version) --hash=sha256:$($_.sha256)"
} | Set-Content C:\CADSGInputs\build-tools.lock -Encoding ascii
& C:\CADSGBuilder\Scripts\python.exe -m pip install --no-index --no-deps `
  --find-links C:\CADSGInputs\tools --require-hashes --no-cache-dir `
  -r C:\CADSGInputs\build-tools.lock
~~~

Use an independent builder directory, never a running App/CDS/production venv.
The commands above prepare that new environment; the build entrypoint itself
only reads it. The target dependency wheel set's transitive completeness is
finally checked by the existing offline installer and its `pip check`, followed
by the complete installed regression suite. Build success is not install success.

## Clean public-source build

Commit the sanitized public source first. A dirty checkout, an untracked file,
a different commit, an implicit epoch, or an epoch different from the commit time
is rejected. The source commit must be the actual eventual public lineage; the
private development history is not a public release source.

~~~powershell
$commit = (git rev-parse HEAD).Trim()
$epoch = [int64](git show -s --format=%ct $commit)
& C:\CADSGBuilder\Scripts\python.exe -I .\scripts\build_gateway_release.py `
  --source-root . --source-commit $commit --source-date-epoch $epoch `
  --build-python C:\CADSGBuilder\Scripts\python.exe `
  --dependency-wheelhouse C:\CADSGInputs\dependencies `
  --tool-wheelhouse C:\CADSGInputs\tools `
  --output C:\CADSGOutput\new-bundle
~~~

The output must be a new directory outside the checkout. Multiple
`--dependency-wheelhouse` or `--tool-wheelhouse` directories are allowed;
conflicting same-name inputs are rejected. The controller creates two independent
`git archive` source snapshots and invokes standard PEP517 `pip wheel` with
`--no-index --no-deps --no-build-isolation --no-cache-dir`. The fixed backend
dependencies must already be installed. Inherited PIP/PYTHON/PYTEST/provider
selectors are stripped; `-I`, explicit epoch and deterministic hashing are used.

Both gateway wheels must have identical bytes. Their package payload must match
the original snapshot bytes exactly. Original source inputs and checkout state
are rechecked. Two matching artifacts do not independently establish that the
source is correct or that App works.

## O169 raw native assembly

Run the public packaging mode in the same clean Windows checkout used for the
gateway build. Keep the reviewed original build manifest in its original
producer output directory and retain its prepared source and Cargo cache.
Supply the reviewed digest as the expected input.

~~~powershell
$reviewedManifestSha = '578681be6b0e187ee703d483ff31ad9a6c8b0da8f9eca91b8761f12f598f807d'
$lock = Join-Path $PWD 'native\p169-source-lock.service-restore-acl-order.draft.json'
& $buildPython -I -B .\scripts\package_native_release.py `
  --public-o169-assembly --source-root $preparedSource `
  --native-manifest $originalBuildManifest --native-manifest-sha256 $reviewedManifestSha `
  --build-lock $lock --cargo $cargo --cargo-home $cargoHome --git $gitExe `
  --output $newPublicStage --evidence-dir $newPrivateEvidence
if ($LASTEXITCODE -ne 0) { throw 'O169 raw native assembly failed' }
~~~

The packager checks the fixed lock, original manifest, source fingerprint, V8
inputs, six exact outputs, and licenses. It emits a raw `.tar.gz`. Archive
metadata retains `built-not-app-qualified` and `App_acceptance_asserted=false`.
The same signed App running from bytes downloaded from GitHub is a separate test.

Stage the gateway ZIP and native tar.gz together under a single proved source
commit. The gateway ZIP includes `release.json`, `wheels/`,
`install_gateway.ps1` and `enter_app_desktop.py`. Keep the exact dependency
source attachment available to support replay. Place `SHA256SUMS.json` beside
the assets with the exact source repository/commit, release tag, filenames,
byte counts, archive hashes and gateway/native manifest hashes. Include the
native build lock digest. Verify the downloaded bytes and real App/rollback
before selecting that exact verified commit and assets for stable delivery.

## Outputs and delivery boundary

- `wheels/`: exact locked dependency wheels and the newly built gateway wheel.
- `release.json`: deterministic manifest using the existing installer schema;
  its full source commit, exact Python target, wheel hashes and declared official
  App/native baseline are verified by that source's own `lifecycle.validate_release`.
- `install_gateway.ps1` and `enter_app_desktop.py`: the installer and standalone
  desktop entrypoint from the same source snapshot.
- `dependencies.windows-py313.json`: the exact committed input/source lock.
- `build-provenance.json`: source inventory, epoch, verified tools, independent
  wheel hashes, payload hashes, manifest hash, installer hash and bootstrap wheel
  hash. The supplied Python executable hash/version are also retained.
- `.logs/`: private build diagnostics. These can contain local paths and should
  not be published automatically. Owned source/build snapshots are removed after
  success; a failure retains its work for diagnosis.

The two independent builds also yield the same manifest bytes when input lock,
source commit, epoch and Python target are identical. Native binaries remain
separately attested inputs; the official bundled native baseline in release.json
is not a claim that the selected patched runtime has that upstream hash. Record
CLI and retained sibling producers separately, as described in native-build.md.

The status is `built_reproducible_not_App_qualified`. Publish only the deliberate
public allowlist above, plus the qualified native artifacts and sanitized reports.
Verify the final delivery archive/installer digest through the trusted release
channel; a manifest digest is not a publisher signature. Never upload `.logs/`
or retained failed work wholesale.

## Development qualification

`--development-qualification` permits a dirty source snapshot for explicit local
qualification. It produces `qualification-release.json` instead of `release.json`
and marks the source commit as the base commit, with the dirty state recorded.
It cannot establish a clean-public-source release. Do not rename that file and
present it as a stable artifact. This mode keeps the current installer schema for
explicit fixture use; it does not make a development artifact safe to publish.

For final delivery, use a clean public source commit and rerun the normal mode.
All required App/runtime gates still block stable publication. Publish after
acceptance; exact-owned temporary-environment cleanup and fresh quota
confirmation follow last.

Official mechanics:
[pip wheel](https://pip.pypa.io/en/stable/cli/pip_wheel/),
[repeatable offline bundles](https://pip.pypa.io/en/stable/topics/repeatable-installs/),
[build isolation](https://pip.pypa.io/en/stable/reference/build-system/).

## Independent dependency artifacts

Dependency wheels are always copied as bytes into newly created files. The builder
never hard-links them or carries their security descriptor into the output.
The pinned input bytes, file identity and link count are checked again after
copying. This keeps the bundle independent of subsequent input writes and lets
output files inherit the selected output directory permissions. Do not fix an
unreadable input by granting broader access to its existing security descriptor.

Build-command failures include an exit code and a bounded, redacted stderr
cause. They do not print full command arguments, stdout or environment values.
A byte-reproducible bundle still requires ordinary-user installation and actual
App qualification; reproducibility alone does not prove file readability.

Release inputs include publicly obtainable wheel bytes or a verified, licensed
source attachment that regenerates the exact locked wheel. Local file URIs and
private qualification wheelhouses alone do not provide a public build input.


## Native and local-App redistribution boundary

This Python bundle contains the explicitly locked wheels, this project's gateway
wheel and installer, manifests and provenance. The six O169 source-built Codex
native programs travel in the separate raw native distribution described above.
Compiled libraries inside dependency wheels keep their original wheel licenses.
The CDS dependency is the complete pinned Python wheel, but the gateway's runtime
import boundary remains the six declared pure protocol modules.

Do not archive an installed gateway/data directory or a prepared native directory.
The official signed desktop App, its ASAR/resources and the Code Mode Host copied
during local preparation are not public gateway-bundle inputs. The baseline JSON
contains hashes and resource names, not those App file contents. Users obtain the
official App separately. Public `app-prepare-runtime` verifies it and locally
prepares the runtime and bundled resources for the selected cohort.

Keep all wheel license/notice files intact. The current installed r4 audit found
108 declared license files present and matching their installed RECORD hashes;
that observation is tied to r4 and does not qualify a changed dependency set.
The separate raw native package must retain its own matching upstream/crate
notices and per-binary producer provenance. A compiler-success directory without
those files is not the complete native release artifact.

Only the explicit public outputs and separately accepted raw native archive
belong in a release. `.logs/`, private build metadata, failed work, user homes,
credentials and sessions remain outside it. Actual App acceptance and final
clean-source archive validation remain separate release gates.
