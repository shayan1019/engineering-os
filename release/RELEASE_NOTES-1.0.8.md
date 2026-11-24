# EOS 1.0.8 - Windows x64

Unsigned. Found by driving real sessions over 75 file types, small and very large
projects, and consecutive, killed and parallel sessions.

## Fixed
- **Second write refused.** In Go, Rust, Java, C, C++, C#, Dart, Vue, Svelte and Arduino projects the first write left the project map stale and every later write was refused.
- **Next session locked out.** A file edited by one session could never be edited by another, nor by the same one a day later. A finished write no longer holds its file; a dead host never does.
- **Parallel writes crashed.** Two tool calls of one message shared temporary files and could approve each other's decision.
- **Ordinary words blocked everything.** `delete`, `order`, `password`, `payment` or `migration` in a request, and files such as `Dockerfile` or `*.mq5`, refused every write in any project.
- **A stale map blocked until the next prompt.** A package install, a git command or the user's editor now causes a re-read. A forged map is still refused.
- **Large projects.** Each source is read once, in parallel, with a time limit; a refresh reads only changes. Measured: 23,306 sources in 53,000 files, first write about 400 s before, 24 s now. Impact analysis has a time limit; when it is hit in a strict project the change is not treated as low risk.
- **Shell.** Build, test and lint commands of other ecosystems run without a prompt, like `npm test`. Installs, deploys, network and destructive commands still ask.
- **Security:** an approved write target no longer approves another command on the same line (`install x && echo y > covered.txt`).
- The engine's own registry no longer claims folders such as `tools` in a user's project.

## Changed behaviour
- Full review tiers now apply to **strict** projects: registered in `PROJECT_PROFILES.json` with `invariants`, or with `"risk": "strict"`. Use `"risk": "normal"` to opt out. Every other project is **normal**: one-file edits are approved automatically, except overwriting an existing secret file.
- Project maps built by older versions are rebuilt on first use.
