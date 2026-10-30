# Actual App qualification: scoped additions

Fixed combination: App **26.1023.6621.0**, frontend **26.1023.51421**, native **0.165.0-alpha.1.2** / upstream `720fec862a608ae1e5878d1eecb04fb93192e93c`, Windows 10 19045. This record closes individual mechanisms, not stable release or universal parity. The 25 installed production files remain unchanged; these additions are passive QA/tests/docs.

## Native question and review

Plan was selected through normal slash UI. One native `request_user_input` call received Alpha from its actual question UI using the same call ID, completed, and returned to Default. An earlier UIA selection did not commit and was not counted.

The App's `/review > Code review > Review uncommitted changes` started an actual native `code_review` turn. This version uses `turn/start` here; `review/start` was not called. DeepSeek reviewed three modified Jinja files and tests, ran **908 original tests** (exit 0), and completed. Four work files and Git status remained unchanged. The earlier task's 22 holdouts were not rerun in review. Prior memory and supervision mean neither result is a blind benchmark.

## Native command-auth refresh

This tests the App-to-gateway developer bearer, not OpenRouter account-key rotation. After warm-up, one target MAIN request received 401. The original credential helper ran exactly once after 401 and before the accepted retry. The same native turn completed without App restart or manual helper invocation. Both complete typed JSON payloads, including all **1,063 fingerprint nodes**, matched.

Raw byte hashes differed and remain recorded. Typed comparison ignores object-key order, insignificant JSON whitespace and equivalent JSON escape spelling. Decoded strings, unknown fields, numeric token spelling/types, arrays and call IDs remain significant. Passive bounded HMAC observation exposes no prompt/credential/key/path values.

An earlier window failed its original age guard before arming/rotation. Another proved refresh without complete-payload evidence. Five non-target scripted budget 503s in the full-comparison window remain failures.

## Fault and navigation limits

- 503 reached natural retry-exhausted failure.
- 429 demonstrated a real retry/Retry-After, followed by actual App Stop.
- 413 and qualified text-cut reached App interruption after real errors/retries; these do not prove natural terminal failure.
- Incomplete tool streaming produced no completed call/result or side effect.
- Genuine process-loss cases verified effect count one and preserved completed tool pairs. A missed earlier hold was not called process loss.
- Canonical same-port recovery invalidated the old owner receipt; the next actual MAIN preserved history.
- Actual App read back all rows of original web source 70 after recovery, with the same reference identity. Provider/model metadata was independently checked.

Final success does not erase intermediate failures or prove all variants equivalent.

## Actual GUI worktree recovery

The App's normal Worktrees settings saved the native snapshot, deleted the owned existing worktree and archived its existing chat. The operator unarchived that same chat and used its normal Restore worktree banner once.

Independent readback matched the complete two-file inventory and hashes, original HEAD, clean status, native Git worktree registration, original chat UUID and unarchived state. No safety-copy restore, manual ref, queue or database repair was used. DeepSeek then called native list_artifacts, found the original active worktree attachment, and ran one native exec_command without a workdir override: actual default cwd, relative reads, both file hashes/sizes and HEAD/status all matched. The same native turn completed.

Scoped review SHA-256: `34fe7551589125c1618d982206d87654d90e663728255bd3e7406033511802f4`.

This closes the formal GUI lifecycle, not the separate secondary restore_worktree tool failure. Earlier missing Invoke patterns and one QA executable-selector error occurred before the intended action, were inspected and retained; they are not product passes or replayed mutations.

## Private evidence index

Raw records remain private. Hashes identify scoped reviews, not certification:

| Record | SHA-256 |
| --- | --- |
| Plan/question | `9513cb9fbf0b9b9afa1d846cf2158accdd81f646d24d7af4a8d05e009824a191` |
| App code review | `7d4c1e3add1e3eb786a37f184923ee3fcf6aa3ed00c10a573d2a1323342892be` |
| Complete auth401 comparison | `9a578747c1ad87bd42c3fb7204253454a699c2c48af7eabd3ae8d502e810f61b` |
| Original age-guard refusal | `1308b6de7a824ee790cbf8fb54a4f90cf0039f46e8743a408081265326473eda` |
| Own MCP cwd / snapshot-key audit | `feb5d9e6df324511362ac98076eea7255de47da1cc532f9539d9c7797632f61c` |

Native desktop/browser operation, archived-worktree tool restore, server-queue consumption, final lifecycle/delivery, bounded cleanup and final fresh weekly-quota verification remain open. Ready MCP/imported module/accepted UI action/empty DB/software-suite success does not close them. Finance/sports private cloud data are not connected or passed; model capability and cloud-account rights remain separate.
