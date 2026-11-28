# EOS NEXUS observation

The desktop process starts this read-only observer through private, inherited anonymous pipes. The observer opens no listener and makes no network or model calls. It reads approved EOS workspaces and the versioned engine registry, with no filesystem writes. A desktop disconnect closes the child's input, which exits independently of EOS.

## Sources and evidence

- Versioned host receipts from the same user's EOS data directory, at <code>EOS_HOME/evidence/nexus/&lt;workspace-digest&gt;/&lt;session&gt;.jsonl</code>, can establish live activity. The digest is the first 24 lowercase hexadecimal characters of SHA-256 over the absolute workspace path with slash normalization, trailing slash removal, and lowercasing on Windows. The file name and host receipt's host field scope each session inside its project.
- Workspace receipts in <code>.agents/evidence/{tools,skills}/*.jsonl</code> are recorded history. A legacy skill receipt can be written before a tool finishes, so its state in the live view remains <code>UNKNOWN</code>. A workspace cannot establish an active session or a gate pass by creating a local log.
- Agent and skill registries provide discovery only. Agent activity requires an observed start with a matching terminal state or a live session. The skill progression distinguishes selection, load and use. A confirmed gate pass requires an explicit verifier receipt with an artifact SHA-256.
- OpenCode skill activation and Claude Code's successful native Skill tool call establish `LOADED`. Codex hooks do not expose a skill lifecycle. A later tool call, agent completion, or session end supplies no separate `IN_USE` or `COMPLETED` evidence for a skill. Those states require explicit host receipts.
- In a watched project, the observer checks Graphify's own `.graphify_build_state.json` against the graph file's size, modification time, and project root. A match exposes an `OBSERVED` Graphify build without opening the graph contents or launching Graphify. The observation is project-scoped and establishes neither a live host session nor source freshness. Declared orchestrators without an execution contract appear as `ADAPTER_ONLY`.
- The observer accepts bounded identifiers, timestamps, counts, timings and known statuses. It omits prompt text, tool arguments and outputs, free-form reasons, raw paths, and model reasoning. Tool metadata and model usage appear only when supplied by the producer. The authenticated desktop stream and EOS user directory trust processes under the same local user account.
- Snapshots contain sorted event history, scoped sessions, registry discovery, and loss counts. They cap projects, sessions, directory entries, total events, per-file tail size and per-poll bytes. Log resets, dropped history, producer loss and malformed rows are reported. A session with no recent active lifecycle receipt is stale. A completed session is disconnected.

## Desktop protocol

Run the bundled interpreter with this script, <code>--stdio --engine &lt;engine&gt; --eos-home &lt;eos-home&gt;</code>, and optionally repeat <code>--workspace &lt;root&gt;</code>. In addition to explicit roots, the observer reads registered workspace roots. The desktop sends a newline-delimited <code>hello</code> JSON message on the private stdin pipe with a randomly generated 32-byte <code>token</code> encoded as lowercase hex. The first output is a schema-1 <code>hello</code> carrying the hex HMAC-SHA256 proof, computed using the decoded token and the UTF-8 string <code>eos-nexus-stdio-v1</code>. The desktop validates it before reading snapshots. The pipe accepts no state changes or EOS commands.

After the greeting, stdout sends schema-1 <code>snapshot</code> lines on change and every five seconds. Each event has an opaque ID and composite session identity. Timestamps sort in UTC, and same-time receipts retain source order. The desktop can restart the observer and reconstruct the bounded window from the logs.

## Verification

On Windows, launch the standalone Codex CLI from a regular terminal for independent EOS acceptance. The packaged Desktop app selects registered Core and relies on a Windows package identity and a provisioning service. A desktop-launched shell can inherit that Core selection. For a standalone test, use a child process without the app-only `CODEX_WINDOWS_REGISTERED_CORE` variable while retaining the native CLI sandbox and the project's `/hooks` approvals. Keep persistent and managed settings in force. [OpenAI Codex registered Core implementation](https://github.com/openai/codex/blob/main/codex-rs/windows-sandbox-rs/src/app_package.rs)

From the source checkout, run <code>python -B -m unittest discover -s nexus -v</code> with EOS's bundled Python or a supported standalone interpreter. For a single diagnostic sample of recorded evidence, run <code>python nexus/observer.py --workspace &lt;workspace&gt; --engine &lt;engine&gt;</code>.
