# R223: native interface audit after P5 deployment

The fixed Windows App combination is still not qualified for stable support.
These checks add evidence to the existing matrix; they do not replace the other
gates or count a native API check as App GUI acceptance.

The official App remains 26.1023.6621.0, with the five-patch native CLI
ebec4115b77c4710feb71d9d460d893bb6cb164c2fbee906087783d83ac685fa.
No additional native compilation was needed for these checks.

## Native Hooks

The actual native API loaded the owned project hook declaration. Normal
config/read and config/batchWrite compare-and-swap operations added only that
project's trust and the three exact hashes returned by hooks/list. Existing
configuration lines and their order were retained. The official App's Hooks
settings use the same native APIs; this did not bypass hook trust.

The real DeepSeek turn exercised the following native path:

- UserPromptSubmit Command injected a nonce absent from the user prompt.
- PreToolUse Command rejected the exact canary apply_patch; the file was absent.
- A later native apply_patch changed the two allowed files.
- PostToolUse McpTool received the actual matching thread, turn, tool-use ID,
  tool input and successful tool response, then returned another context nonce.
- The terminal answer repeated both nonces; the unrelated dirty file was intact.

The initial verifier incorrectly required phase=final_answer. The native protocol
declares phase optional, and this turn's actual terminal summary contained a null
phase. The failed verifier result remains. Independent verification required the
terminal summary message to equal the same turn's final item/completed message
by ID and complete payload, and separately verified hook order, exact calls,
receipts and filesystem effects. The task was not replayed.

This closes the stated native API Hooks scope. Actual App Hooks execution and
file diff/Undo remain separate. Exact owned trust entries are retained for that
GUI check and have a compare-and-swap rollback plan.

## Strict outputSchema and real tool use

The first native API test returned valid JSON but skipped the required file read
and used an empty nonce. The task oracle rejected it. A later same-turn test with
a nonce pattern and explicit tool instruction again skipped the tool and invented
a pattern-valid nonce; it was also rejected.

A two-turn control passed: the native command first read an unknown file nonce;
the next turn in that same thread used outputSchema and returned raw JSON with
the exact observed nonce. Permission policy stayed on-request, workspace-write
and auto_review. Configuration was unchanged during these checks.

Independent direct OpenRouter probes used the fixed model and each explicitly
allowed provider, with allow_fallbacks=false and require_parameters=true:

| Route | Schema + auto | Schema + required | Plain + auto |
| --- | --- | --- | --- |
| deepseek | 404, parameter support rejected | 404, parameter support rejected | Real function call and observed nonce final |
| morph/fp8 | JSON final, no call, invented nonce | Real function call; after its real result, schema final matched nonce | Real function call and observed nonce final |
| primeintellect | JSON final, no call, invented nonce | Real function call; after its real result, schema final matched nonce | Real function call and observed nonce final |

These are protocol probes, not App acceptance. They show that tools were not
categorically unavailable with schema, and that the zero-call behavior also
occurred without this gateway. They do not justify silently changing auto to
required. Fourteen actual requests, including tool-result continuations, are
retained without implicit retry.

An isolated candidate will check whether the existing final-only strict validator
can preserve the native contract while the upstream automatic tool path receives
plain output plus the complete schema instruction. Any such mapping must be
explicit in receipts; it must not be described as upstream constrained decoding.
Invalid final results must fail before native completed publication. No candidate
result is counted here, and the current production gateway has not changed.

## Windows worktree model restoration

Independent readback reconfirmed the failed model-tool path: snapshot creation
hashes the slash form of a root, whereas restoration hashes the archived
backslash form of the same Windows directory. One snapshot key exists and the
other does not. The signed original App ASAR, main and Git worker still match
their recorded hashes.

The failure occurs inside the closed App's restore worker, outside the model
gateway. Public Rust attachment storage treats the identity and payload as opaque.
Changing model arguments alone does not repair the archived lookup; platform
normalization and existing attachment identities also prevent slash input from
being a general remedy.

Normal GUI restoration passed separately. The model restoration defect remains
open. No ref alias, opaque payload rewrite, replacement worktree implementation
or closed App modification was introduced. Original, code-free reproduction
steps and a minimum upstream identity-normalization specification are retained.

## Evidence and unfinished work

Private raw events, exact requests, source hashes, file hashes, compare-and-swap
configuration receipts, negative results and direct upstream responses remain on
the Windows host. They contain owned fixture data and are not copied into public
documentation. The active execution-state index records their paths and hashes.

GUI fork/history clipping, inherited web references and cold continuation are in
progress. App Hooks/diff/Undo, the schema candidate, prior open matrix gates,
publication, final bounded cleanup and the last live quota check are unfinished.
