# Project boundaries

All development and execution for this repository occurs on Windows. The Mac
controller is SSH transport only; do not read or modify its files or credentials.

This gateway adapts model protocol and credential/configuration integration. Do
not import CDS launchers, stores, process supervisors, native transport, tool
execution, or agent orchestration. Keep Codex App's native runtime responsible
for threads, tools, sandbox and approvals. Pure protocol dependency is pinned.

Do not operate on any pre-existing CDS process or another user's desktop session.
Never kill by executable name. Match this project's PID and creation time before
stopping a service. Do not log credentials, bearer tokens, raw request prompts or
unredacted provider errors into public artifacts.

Use the fixed App/native combination in docs/acceptance-matrix.md. A CLI test is
not an App test. Do not publish stable support before all in-scope gates close.
Keep previous failure evidence. Do not silently change provider/model on failure.
