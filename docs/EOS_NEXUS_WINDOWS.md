# EOS NEXUS on Windows

Build the native desktop executable with `nexus/desktop/build.ps1`. The Windows installer build compiles it and stages the observer with it. The CLI installs both inside the versioned, hashed engine. `eos nexus --print` verifies component hashes and reports the active version. `eos nexus` opens the window. `eos nexus --workspace <path>` selects a local workspace directly. The Start menu shortcut uses the same launcher.

When the panel is open, use its preferences to enable auto-open, tray behavior, reduced motion, low-resource mode or Windows startup. Early builds default to manual opening. A tray icon can reopen the window. Closing NEXUS releases the observer and has no connection to EOS process control. For an explicit source build, supply `--workspace`, `--engine`, `--python`, and `--observer` to the native executable if the candidate is outside the installed layout.

## Host evidence

OpenCode v2 runs its existing activation plugin and sends events from native lifecycle hooks. `eos open <path> --host claude` prepares additional settings scoped to that host run and preserves the user's settings files. For an explicit `claude` run, generate these settings with `python engine/tools/eos_host_hooks.py config claude`, pass the resulting JSON file with `--settings`, and set `EOS_NEXUS_WORKSPACE` to the authorized project root.

`eos open <path> --host codex` registers hooks in the project when `.codex/hooks.json` is absent. It keeps an existing configuration intact. For projects with an existing file, generate the EOS definitions with `python engine/tools/eos_host_hooks.py config codex --shell powershell` and merge the entries. Trust the project and approve the exact hook definitions through Codex's native `/hooks` interface. For a manual host invocation, set `EOS_NEXUS_WORKSPACE` to the authorized root. Hosts with no corresponding hooks can still use `eos activate` or `eos route` for the shared discipline and show recorded EOS evidence.

NEXUS uses the evidence a host actually reports. A selection receipt does not imply a native skill load. An active agent requires a host execution event. Model, provider, usage and cost fields may be absent. A quality-gate result requires verification evidence. The timeline labels archived, demonstration, stale and disconnected events independently of live activity.

An independent verifier can reference a quality-gate artifact from an existing host stream with `python engine/tools/nexus_event.py gate <host> <stream-session> <artifact>`. Use the session's JSONL filename stem and run from the EOS workspace or set `EOS_WORKSPACE` explicitly. The command verifies the artifact and projects its digest as unbound evidence. A session gate PASS requires a receipt with explicit session provenance.

## Local data

The host writes bounded segments of projected metadata under the EOS user data directory. The observer reads these records and bounded legacy evidence tails, with no write path to EOS policy. The desktop starts the observer over private anonymous pipes and authenticates it before accepting a snapshot. The panel does not transmit prompt bodies, tool arguments, tool output, or model reasoning. UI settings and sanitized replay archives are separate from the versioned engine.

## Checks

```powershell
python -m unittest discover -s engine/tools -p test_eos_nexus_activation.py -v
python -m unittest discover -s nexus -v
node --test engine/host-plugins/opencode/eos-activation.test.mjs
```

Run `nexus/desktop/build.ps1` and `eos nexus --print` separately on Windows to verify the executable build and installed hashes.
