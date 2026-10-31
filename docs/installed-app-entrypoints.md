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
user. It does not launch the App. Runtime preparation, binding and configuration reject an App using
this gateway's dedicated native home, Electron or browser profile; close only
that owned instance before changing it. A disjoint stock or separate-profile App
may continue working. Unknown identity/profile observations fail closed for
inspection, with no process takeover or automatic termination. Native C/D names
or developer paths are never inferred. Preserve this native directory as an
external release input.

After a gateway upgrade, explicitly bind the selected compatible native release
again. An unknown App update, signature, commit, binary or manifest fails closed.
There is no automatic update, download, provider fallback or process takeover.

The explicit `app-bind` upgrade also accepts the previously shipped
26.1016.8120.0 / 0.163.0-alpha.11.1 cohort when its exact known baseline and
original managed installation are still present and verified for this data home.
Normal check/configure/launch operations continue to reject that old binding
under the new release. The upgrade preserves the old binding, installation
receipt and baseline under `app-binding-backups/<transition-sha256>` before
publishing the new selection. It clears the previous model catalog: run
`app-configure` with the new version-matched catalog before starting the App.
The command returns `prior_cohort_backup` and
`catalog_reconfiguration_required` for this transition. It does not delete a
binding, select a replacement runtime automatically, or modify native sessions.

For an explicit downgrade, retain that backup together with the old installation
and native directory. The transition directory is keyed by SHA256 of the original and planned binding
hashes, so a later upgrade after rollback retains its own recovery record.
Its `migration.json` records both the original binding
SHA and the exact planned replacement SHA. Restore `binding.before.json` only
while the current binding still matches `planned_binding_sha256`, using an atomic
compare-and-swap write; then use the retained release's normal rollback and
configuration checkpoint workflow. If the binding has changed since the upgrade,
stop and preserve the later explicit selection. Never copy an old binding over
an unknown current file or remove it to bypass compatibility validation.

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


### Explicit native agent version

The default remains native V2. To exercise the original native V1 close/resume
mechanisms with the same fixed DeepSeek model, build a separate catalog from the
qualified native binary's bundled metadata. Use the fresh installed candidate's
Python, not a source checkout or an older development environment:

    $native = '<qualified bound codex.exe>'
    $python = '<fresh installed candidate venv/Scripts/python.exe>'
    $bundled = '<new bundled-models.json>'
    $catalog = '<new DeepSeek-native-v1.json>'
    $nativeVersion = (& $native --version).Trim()
    & $python -I -c "import subprocess,sys; from pathlib import Path; raw=subprocess.run([sys.argv[1],'debug','models','--bundled'],check=True,stdout=subprocess.PIPE).stdout; Path(sys.argv[2]).open('xb').write(raw)" $native $bundled
    & $python -I -c "import json,sys; from pathlib import Path; from codex_app_deepseek.catalog import build_catalog; source=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig')); result=build_catalog(source,native_version=sys.argv[3],multi_agent_version='v1'); Path(sys.argv[2]).open('x',encoding='utf-8',newline='\n').write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')" $bundled $catalog $nativeVersion
    $catalogSha = (Get-FileHash -LiteralPath $catalog -Algorithm SHA256).Hash.ToLowerInvariant()

Review and retain that exact SHA as the trusted catalog input, then configure
through the existing immutable-copy/checkpoint path:

    & $cdag app-configure --catalog $catalog --catalog-sha256 $catalogSha --multi-agent-version v1

Both `app-configure` and low-level `configure-app` accept
`--multi-agent-version {v1,v2}`, defaulting to `v2`. The requested version must
match the supplied catalog's unique visible pinned model. Configuration validates
those bytes; it does not rewrite the trusted catalog or silently select another
mode. The generator changes only that main entry's `multi_agent_version`;
inference model ID, role, security/instruction data, tool mode and all hidden
auxiliary metadata remain unchanged. No new routing alias or gateway policy is
needed. Generate with `multi_agent_version='v2'` (or omit it) to retain the default.

**Start a fresh chat for the selected version.** Existing chats retain their
recorded native agent version; changing their catalog is not a migration.
V2 interruption/reuse and V1 durable close/resume require separate actual-App
qualification. Generating or configuring a catalog alone proves neither.

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


## Bounded observation on a slow Windows cold start

The normal launch entry has separate bounded observation and caller-wait
settings. Both default to 60 seconds; both accept an explicit value up to
120 seconds. On the qualified Windows host, one cold H launch created the
correct App immediately but its first verified backend appeared after about
69 seconds. The original launch observation timeout was preserved, and a
separate normal status read verified that same instance. No second App was
started to hide the timeout.

For a Windows cold start on a host with this observed startup delay, use:

    & $cdag app-launch --observation-seconds 120 --wait-seconds 120

These settings extend only the bounded observation window. They do not retry
App creation, authentication, model requests or tools. If observation expires,
retain the original receipt and use app-status to reconcile the recorded
instance before deciding whether another launch is necessary. Do not change
old plan/receipt hashes or terminate a process by executable name.
