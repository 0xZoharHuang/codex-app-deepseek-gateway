# Explicit image transport and web media policy

Status: integrated entrypoints under qualification. This page does not declare App acceptance.

The native runtime remains responsible for image history, compaction, model turns,
thread recovery and tool execution. The optional transport changes only the outgoing
HTTP representation: exact image bytes can be uploaded to the authenticated
OpenRouter Files service and referenced privately in the same inference request.
It does not replace, downsample, combine or remove native images. Files and model
requests use the same request-bound credential and explicitly configured proxy.

Configure `gateway.json` explicitly, for example:

```json
{
  "image_search_backend": "wikimedia_commons",
  "image_transport": {
    "activate_above_bytes": 25165824,
    "wire_budget_bytes": 31457280,
    "max_file_references": 16,
    "idle_ttl_seconds": 3600,
    "verify_after_seconds": 60,
    "lock_timeout_seconds": 120
  }
}
```

These are operator prevention limits, not a claimed universal upstream capacity.
Absent/null fields leave each optional backend unselected. `wikimedia_commons`
means the Commons file index with source/license metadata, not OpenAI's image
index. Search does not automatically switch between indexes on failure.

The mixed transport respects the observed 16-reference request limit, including
existing references. It selects the largest exact byte savings and retains other
images inline, in their original positions. The 34,009,154-byte/101-image fixture
was successfully sent as 28,625,730 bytes using 16 private references and 85 inline
images; its real provider was Morph on the pinned DeepSeek version. This is one
measured route and body, not a claim about every possible history size. A body that
cannot fit the configured budget fails explicitly before inference, preserving
native history for compaction. An unacknowledged upload is never blindly repeated.

Private cache metadata lives only in this gateway's `image-transport` directory,
with current-user/SYSTEM access. No raw images or credential values are stored in
that journal. Credential scopes are distinct. Remote files do not expire by
promise: deletion requires explicit maintenance and confirmed absence.

```powershell
codex-app-deepseek image-cache-status
codex-app-deepseek image-cache-cleanup
codex-app-deepseek image-cache-cleanup --all-unpinned
codex-app-deepseek image-cache-reconcile-uploads
```

Status reports ownership and unknown outcomes; it does not call a model. Normal
cleanup retains recently used files and any request with unproved provider
termination. `--all-unpinned` requests deletion of every safely released own file.
Maintenance returning `complete=false` exits with code 3 and retains its journal.
Credential/API/configuration failures are errors, never clean-state reports.

Upload reconciliation is a separate explicit operation. It scans at most four
50-record official file-list pages, filters in memory for the exact private random
upload nonce and metadata, and neither returns unrelated metadata nor reads their
contents. A complete, unique match becomes cleanup-only, not trusted cached image
content. Partial pages, duplicates, missing matches or rate limits retain the
unknown outcome. No inference is replayed by any maintenance command.

Completed/failed/incomplete provider terminal evidence is distinct from delivery
to App. A disconnected client or stopped gateway cannot be used as proof that the
provider has stopped consuming a file. Such requests keep their pins until their
actual outcome is reconciled. Removing the local cache does not clean remote files.

PDF documents are fetched under the backend's public-network policy, then parsed
or rendered in a bounded subprocess. On Windows its exact suspended process is
admitted to an anonymous Job before it can run. The worker inherits no credential,
proxy, Python or Codex environment and accepts only fixed operations and stdin
PDF bytes. See `web-media-handoff.json` in private evidence for the measured limits.

## Native App display references

The fixed local Codex App preserves opaque standalone-web result metadata but
does not automatically turn its image_v2 records into native message images.
The gateway therefore also returns a standard Markdown image destination in the
actual tool text. Captions are fixed; a source title cannot inject Markdown.

For a PDF screenshot, only the already-rendered, digest-verified PNG is atomically
published under web-data/generated-media/<sha256>.png with private permissions.
The file is an immutable derived cache within the same byte/count limits as
retained sources. Wrong existing bytes, links, junctions or exhausted budgets
produce a failure, never a display-success claim. Files persist across normal
gateway/App restart so a previously returned absolute destination remains useful.
No new endpoint, tool, renderer or native thread logic is introduced. The App's
own read-file-binary path remains responsible for accessing and displaying it.

Commons image results expose the provider's actual thumbnail URL in standard
Markdown. The App's remote-image safe-URL policy remains authoritative. Remote
images are not downloaded into the local generated cache to bypass that policy.
A turnNimage reference identifies a backend source; it is not an image URL.
Returning a valid reference is not evidence that the App displayed its pixels:
actual GUI observation remains required.


## What the image transport counters prove

The current image transport planner counts supported inline data URLs in typed
input-image parts. Its image_occurrences field is not a total of every image
kind in an incoming request. A typed HTTPS image can remain in the forwarded
payload while contributing zero to this inline-planning counter. Likewise,
zero uploads means no private Files conversion was needed; it does not prove
that no image was sent.

The private-reference consumption status concerns uploaded-file lease tracking.
A not_applicable status with no private references is not proof that a model
did or did not receive pixels. Do not infer vision success, image loss or model
quality from those values alone.

For delivery acceptance, separately bind the native tool output and UI event,
the next actual native request's typed-image structure, the translated outbound
structure, and the provider response/usage when available. A small local image
can remain inline; URL images and private file references require their own
type-specific evidence. Record only counts, schemes and hashes in diagnostic
artifacts, not bearer headers, private URLs, raw image bytes, prompts or reasoning.
