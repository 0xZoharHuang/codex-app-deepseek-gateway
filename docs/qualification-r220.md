# Actual qualification additions: native condition consumers and lifecycle

This supplements [the earlier scoped mechanisms](qualification-r219.md). The fixed App/frontend/native combination and local runtime contract are unchanged. Stable publication is still pending.

## Read the actual consumers, not SDK internals

Two bounded read-only observations used exact original return breakpoints in the unchanged App renderer. The running script hash matched the signed original ASAR resource. The controller did not recompute SDK getters, call the feature functions, replace values, submit a model turn or edit policy.

| Original consumer | Actual return | Scope |
| --- | --- | --- |
| Browser Use condition | Boolean false | This invocation's effective value |
| Computer Use condition | Boolean false | This invocation's effective value |
| Server queued follow-ups | Boolean false | An existing SDK client was present |

The first window observed the two Xb consumers but did not observe queue; a second narrow window observed Pg while navigating to an existing chat. Each pause lasted less than 0.02 seconds. All owned breakpoints were removed, debugger disabled and sockets closed. The temporary debug App was closed normally, and the ordinary installed launcher was used again.

Normal Computer Use/Browser settings also showed disabled controls. These results establish the effective local conditions in this run. They do not identify a rollout, admin, account or geographic cause, and are not positive browser/computer/server-queue execution tests. Existing positive client Queue/Steer results remain separate. Earlier zero-hit SDK observations remain recorded and are not the basis for these values.

The distinct Windows opt-in experiment still failed at the App-owned desktop pipe; it is documented in [the bootstrap investigation](upstream-native-computer-use-bootstrap.md), was not installed as a production workaround and did not modify the App package or system environment.

## Current offline artifact and real lifecycle

The clean source commit `a26beb06e1972b710ff0f6b286422964981ca8a2` built with the public offline builder, using its two independent source snapshots/wheel/manifest builds. The resulting manifest was `568c1e5931cb23a48d6e10cedd693d2dc0570014cf2ce750637168c2a578fe3f`. The entire 25-file product payload matched the installed qualified payload. Later report-only commits do not automatically become this artifact's source.

A separate ordinary-user installation then exercised the real public installer and service entrypoints:

1. Install/activate A, configure a private fixture and register its checkpoint; start the real owned service and verify authenticated health, then stop it.
2. Install B without changing A's activation; activate/configure B and verify real service start/stop.
3. Activate retained A and verify its service; activate B again.
4. Run the public three-way configuration rollback, retaining the original annotation and a later user-added project/trust section.
5. Reconfigure B, start its real service, and use the full public uninstall while that service is running.
6. Reinstall B, activate and start/stop it. Retire the temporary B and retained A using the public uninstall entrypoint.

All public lifecycle command phases returned zero. Independent readback found no active spare, owned listener, service process or service task. Session/project preservation fixtures remained byte-identical, and user configuration additions remained. The existing project App, native backend, canonical gateway and configuration were checked unchanged throughout.

A and B have the **same product payload** and different release/source metadata. This verifies installation/activation/rollback/removal mechanics, not a changed product implementation. The fixture was gateway-only and App-unbound: no second GUI or native sandbox was provisioned/removed. Earlier bound-native cleanup evidence remains separately scoped.

## Remove a stale owned acceptance entry

Normal startup logs exposed an old project-owned OAuth fixture still configured after its loopback service had ended. With the project App closed, the original native mcp logout/remove commands removed exactly that fixture and its stored login. Decoded configuration differed only by removal of the exact fixture entry; other MCP servers, plugins and user settings remained. Original OAuth acceptance evidence was retained.

The first private cleanup controller exited before any native command; its original exception was not captured. That failure is retained rather than assigned a proven cause. Follow-up read-only checks passed the original pre-mutation guards and showed a high-created directory with inherited permissions and no explicit ordinary-user ACL-management grant. The directory was made private to that same user with the existing helper, then one observed native logout/remove pair completed. No machine security policy or Defender setting changed.

## Private evidence references

| Scoped independent review | SHA-256 |
| --- | --- |
| Native consumer returns | `5917a8bf66ba0d511db819f000755292712d1357976353d03bf075592c9ede2c` |
| Same-product lifecycle | `268cdea9b787bc1253531a6e3894f3cbaa2ee665e334bd0c647d2856fd92c3e4` |

Hashes identify private scoped records, not an external certification. Worktree tool-entry recovery and actual native desktop/browser operation remain distinct from the passed formal GUI worktree recovery. Public stable delivery, final semantic cleanup and the final fresh weekly-quota verification remain pending.
