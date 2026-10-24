# Use the installed Windows gateway and private App

These commands use the active managed installation. They require no source checkout,
developer venv or global PATH edit. They reuse the existing native runtime and
official-package launcher, without supervising the App or implementing an agent loop.
Actual App/runtime qualification remains a separate release gate.

## Select the installed command

Install and Activate return a command field pointing to the installed executable.
In a non-elevated PowerShell on the intended Windows desktop:

    $cdag = '<command returned by Install or Activate>'
    & $cdag --version
    & $cdag setup-credentials

The credential command reads the key interactively into this project's Windows
credential target. Do not put it in an argument or log auth-token output.

The wheel includes the fixed App baseline and this project's package-launch script.
Their hashes and the complete installed release inventory are checked. The official
closed App, native binaries, accounts, cookies and user profiles are not in the wheel.

## Select the explicit native release

Extract the separately verified open-source native release into a permanent private
directory, for example the gateway data home's native-runtimes directory. Supply
its raw manifest and separately trusted SHA from the release channel:

    & $cdag app-prepare-runtime --native-manifest '<native-directory>\manifest.json' --native-manifest-sha256 '<trusted raw SHA>'

This verifies the installed official App/signature/resources and copies only its
matching Code Mode Host sidecar into the candidate directory. It runs no native/App
code or sandbox setup. Bind the returned prepared manifest and SHA:

    & $cdag app-bind --native-manifest '<prepared manifest>' --native-manifest-sha256 '<prepared SHA>'

Binding records the explicit selection for the active gateway release and current
user. It does not launch the App. Quit this user's Codex App first; no other user is
logged out. Native C/D names or developer paths are never inferred. Preserve this
native directory as an external release input.

After a gateway upgrade, explicitly bind the selected compatible native release
again. An unknown App update, signature, commit, binary or manifest fails closed.
There is no automatic update, download, provider fallback or process takeover.

## Configure installed authentication

Supply the version-matched model catalog and its trusted release SHA:

    & $cdag app-configure --catalog '<release catalog.json>' --catalog-sha256 '<trusted catalog SHA>' --port 42817

This saves immutable catalog bytes under the gateway data home, points the dedicated
App configuration at the verified installed `venv/Scripts/python.exe` with official
`auth.args = ["-I", "-m", "codex_app_deepseek.cli", "auth-token"]`, and registers its
rollback checkpoint. Python isolated mode ignores inherited `PYTHONPATH`/`PYTHONHOME`
and the working directory when resolving the authentication module. No source checkout
or new launcher process system is involved. The catalog download/source is no longer a runtime dependency.

The dedicated App home is %LOCALAPPDATA%\codex-app-deepseek-app. The default .codex
profile is not selected. Low-level configure-app now has the same private default
and retains its explicit --config argument. Prefer app-configure for managed installs.

If configuration applies but registry writing fails, the command reports an error
and the actual checkpoint path. Preserve and register that checkpoint through the
existing register-checkpoint lifecycle entrypoint before rollback/uninstall. Partial
application is not reported as success.

## Operate the gateway and App

    & $cdag service-start --port 42817
    & $cdag diagnose
    & $cdag app-check
    & $cdag app-launch
    & $cdag app-status

service-start/stop reuse the existing current-user Limited task and exact process
ownership checks. app-launch does not implicitly start or adopt a gateway.
app-check verifies the provider's installed auth/catalog paths and requires its URL
to match the installed release's owned loopback listener and authenticated health.
It makes no upstream model request. The checked configuration bytes are pinned by
SHA256 into the subsequent one-shot launch request; both package admission and the
inside-package process creation reject any intervening configuration change.

app-launch calls the packaged official-package launcher once using the installed
interpreter and fixed private home/browser settings. It accepts the existing optional
--accessibility flag. It does not restart/kill an App. Use normal native App onboarding
and sandbox setup when required; this layer does not replace them.

An unconfirmed launch returns nonzero and is not retried. app-status observes the latest
owned receipt using exact PID/creation time, or --run-id selects a previous receipt.
Unknown liveness is not running. A caller timeout can leave the App/startup observer
alive: inspect status and close it normally before a new launch.

--install-root selects an explicit managed install. Product operations require that
installation's actual active interpreter; a source venv, inactive release, or copied
package cannot silently control a different installation.

## Stop and roll back

    & $cdag service-stop

This stops only the owned gateway and may interrupt an App request using it.
Close the private App normally and preserve its sessions/projects. Use the verified
release installer's Rollback/Uninstall actions for complete configuration restoration
and external two-process cleanup; see lifecycle.md. Do not kill codex.exe by name
or copy this private profile back into the default App.

These entrypoint checks do not establish full runtime parity. Protocol fixtures,
real upstream routing, native mechanisms and actual GUI tasks retain separate gates.

Binding changes and configuration serialize through the existing installation and per-user launch locks. Configuration reads the current binding only after acquiring those locks, so a completed concurrent bind cannot be silently replaced by stale state. The installed launch command requires its checked configuration SHA; older low-level developer calls may omit that extra field and retain their existing per-plan recheck.
