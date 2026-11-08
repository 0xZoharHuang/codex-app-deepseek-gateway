# Offline App image-cache format maintenance

The App gateway uses the pinned CDS **pure image-cache class** for cache format,
per-asset locks, short ledger transactions, lease settlement and lossless budget
planning. Its own aiohttp Files backend and App lifecycle remain separate.
The planner counts referenced file bytes per occurrence under the verified 20 MiB
OpenRouter limit, including repeated references. Existing references in the
internal provider payload require authoritative size metadata. An unresolved
selection reports unknown feasibility and stops before inference. This
command does not read credentials, create HTTP clients, call Files, send inference,
or execute Codex/native helpers.

## Upgrade an existing installation

1. Let the current App task, tools and child work finish, then quit this project's
   App normally. Finish any standalone gateway/image-cache command for this data
   home. Do not stop processes by executable name.
2. Stop/reconcile this installation's gateway with its normal service-stop command.
   Maintenance refuses a retained service record or an occupied service lease.
3. Use the active installed gateway entry, with its explicit installation root:

       & $gateway image-cache-format --install-root $managed --target-version 3

   An old App cache without cache_identity is not silently adopted. Only when its
   known legacy assets, pins and receipts tables are all empty may you explicitly
   enroll it:

       & $gateway image-cache-format --install-root $managed --target-version 3 --enroll-empty-legacy

Nonempty unbound state, foreign ownership metadata, unknown schema, an ambiguous
project actor or an unretired managed actor remains an error. Empty enrollment
does not establish ownership of nonempty uploads, reinterpret legacy float PID
births, or discard unknown consumption pins.

The command reuses the existing installation/lifecycle, App launch mutex and
service leases. It checks retained App/native lifetimes and relevant selectors,
bound native images, explicit standalone App CLI home arguments, and unreadable
private project interpreter images. A final actor and empty-enrollment check runs before the shared cache's first
salt/format metadata publication, including a fresh root's first salt. At that point it
already holds initialize, all scope and existing asset locks. That closes the
cold-reader and late-empty-ledger gaps before the legacy salt fence is published.
The reported enrolled scope set comes from actual completed identity transitions,
not only the earlier preflight list.

A generic Python/codex basename alone is not project attribution. Disjoint
readable CLI homes and unrelated processes without source/path/selector evidence
are not adopted or stopped. This observer does **not** discover arbitrary
embedded-library callers. If you run custom code against this private cache,
drain it explicitly before maintenance; do not treat this command as a global
Python-process inspector. Source-matched unknown actors are refused.

## Completion, interrupted transitions and rollback

Exit 0 requires complete=true. A definite incomplete migration returns exit 3;
precondition/identity errors return exit 2. CLI success, a partial set of migrated
scopes, or an existing salt backup alone does not prove completion.

All scopes are checked before any format publication. A preflight/prewrite
rejection leaves the original salt and database bytes unchanged. After a format
transition has started, a later scope/publication failure may leave partial
identity progress and a transition marker. Historical assets/pins/receipts
projections remain preserved, the original 32-byte salt stays in its private
backup. A durable long salt fence prevents new legacy readers from opening a new
credential scope. A transition marker by itself only stops the new reader: old
App readers ignore that marker. If failure precedes fence publication, keep old
actors drained rather than assuming they are fenced. Neither a transition nor an
unknown actor is turned into a successful result.

Preserve the original error and state. Resolve the cause, drain actors again and
explicitly continue to the same target, including empty enrollment if remaining
legacy scopes still need it. Do not delete the marker/salt or regenerate a salt
to force progress.

If deliberately returning to an older gateway, drain the App, gateway and
standalone callers again, then run:

    & $gateway image-cache-format --install-root $managed --target-version 2

Rollback changes format metadata; it does not replay a saved database snapshot.
The original salt is restored only after every ledger has durably reached format
2. Legacy mode retains its older global-lock concurrency behavior. Requalify
format 3 before returning to the new concurrent gateway. No migration or rollback
automatically activates a gateway, launches an App or removes user history.
