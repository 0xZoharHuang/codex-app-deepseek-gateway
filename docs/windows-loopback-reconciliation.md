# Windows native loopback reconciliation

P20 is a Windows-only source revision of the fixed Codex 0.165 runtime. It keeps
native tools, restricted tokens, threads, approvals and proxy implementation in
the Codex runtime. The gateway protocol implementation is unchanged.

## Why the kernel policy is necessary

Real Win10 tests found two different failures. An older gateway runtime had left
persistent, home-scoped WFP loopback blocks behind; P19 updated its marker and
Firewall COM rules but did not retire those objects. Separately, a fresh P19
namespace with correct COM rules still allowed TCP loopback and socketpair in the
restricted profile. Deleting the old rules alone would therefore loosen the
restricted profile.

P20 reconciles the already reviewed native ALE loopback rules in one WFP
transaction, using the effective native provisioning request:

| Effective profile | Native loopback enforcement |
| --- | --- |
| `allow_local_binding=false`, no proxy ports | Block outbound loopback TCP and UDP, and inbound loopback TCP and UDP |
| `allow_local_binding=false`, explicit proxy ports | Permit those outbound TCP proxy ports by excluding them from the block; keep other TCP, UDP and inbound restrictions |
| `allow_local_binding=true` | Remove this home's additional loopback restrictions |

The original twelve ICMP/DNS/SMB WFP filters and the native COM non-loopback
restrictions remain in force. The table does not override those original rules.

## Upgrade, collision handling and uninstall

The normal native provisioning path applies COM configuration, then performs
mandatory WFP reconciliation before committing its setup marker. Marker version
8 invalidates older 5/6/7 state even when network settings otherwise match.
Reconciliation errors propagate independently of the original best-effort
supplemental WFP installation path.

Retirement requires the exact validated home namespace, provider and dedicated
sublayer, native target-account descriptor, derived filter keys and known rule
shapes. A nonempty collection must contain the complete atomic legacy rule set,
with equal IPv4/IPv6 TCP complements. Unknown, partial or foreign collisions are
preserved and cause an explicit failure. Runtime sublayer weight is recorded as
ordering information rather than treated as an ownership identifier.

Repeated reconciliation is idempotent. Uninstall validates and removes the
home-scoped WFP state transactionally before deleting its state or identities;
ambiguous objects cannot be silently discarded. No Defender exclusion, global
firewall reset or manual pre-clean is part of this implementation.

## Correct cached helper selection

The accompanying helper-cache patch compares possible reused files against the
selected source in bounded 64 KiB buffers. Equal version names, sizes and
modification times no longer authorize different executable bytes. Replacement
continues to use the native atomic persistence path: a locked stale file is
preserved when replacement cannot succeed. This applies to actual consumers of
that materialization routine; other executable paths require their own identity
readback.

## Qualification boundary

Build and assemble P20 with the explicit `native/build-lock.O165-P20-source.json`
through the normal commands in [native-build.md](native-build.md). The historical
P19 lock and its original failure evidence remain separate. An older COM-only
runtime is not an equivalent restricted-mode fallback.

Source checks and native component tests do not qualify the desktop App. The
final combination also requires the normal six-program package, actual cached
helper identities, a successful normal provisioning transition, and real App
restricted/proxy/local-binding/return-to-restricted tests. Candidate evidence must
state which of those stages actually passed.
