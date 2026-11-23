// EOS write guard for OpenCode V2.
//
// Why this file is separate from eos-activation.js
// ------------------------------------------------
// Activation injects context. This file can *deny a tool call*. Keeping them
// apart means a broken enforcement hook cannot silently take always-on
// activation down with it, and an activation regression cannot silently disable
// the write guard.
//
// What it enforces
// ----------------
// 1. `write`, `edit`, `patch`, `multiedit`, `apply_patch` are refused unless an
//    APPROVED_FOR_EDIT (or IMPLEMENTED) Engineering Decision Record covers the
//    target path for this workspace.
// 2. Shell commands that can write are classified before execution. A command
//    that is not provably read-only is refused. This closes the "use python -c
//    to write the file" bypass that a path check on `write` alone would leave.
// 3. Every decision is appended to .agents/evidence/tools/<session>.jsonl, so
//    "the tool ran" is evidenced, not claimed.
//
// What it deliberately does not do
// --------------------------------
// It does not create decisions, does not approve them, and does not run tests.
// It reports BLOCKED when the engine or the hook surface is missing; it never
// pretends to enforce something it cannot.
//
// Host API (OpenCode V2, verified shape used by eos-activation.js)
// ---------------------------------------------------------------
// ctx.session.hook(name, fn), ctx.tool.hook(name, fn), ctx.permission.hook(name, fn).
// Registration is feature-detected. A missing surface is recorded as
// `hook_unavailable`, never as success.

import { spawn } from "node:child_process"
import { appendFileSync, mkdirSync, existsSync, readdirSync, readFileSync } from "node:fs"
import { createHash } from "node:crypto"
import { dirname, join, resolve, sep, isAbsolute } from "node:path"
import { tmpdir } from "node:os"

const TIMEOUT_MS = 8000
const CACHE_MS = 1500
const MAX_REASON = 400

const WRITE_TOOLS = new Set([
  "write", "edit", "patch", "multiedit", "apply_patch", "write_file",
  "edit_file", "create_file", "str_replace_editor",
])

const READ_TOOLS = new Set(["read", "read_file"])

const SHELL_TOOLS = new Set(["bash", "shell", "sh", "zsh", "powershell", "pwsh", "terminal"])

// A command is read-only only when it matches this list. Everything else is
// refused: a false allow writes a file, a false deny asks a human.
const READ_ONLY_PREFIXES = [
  "git status", "git diff", "git log", "git show", "git branch", "git rev-parse",
  "git ls-files", "git remote -v",
  "pytest", "python -m pytest", "python -m unittest",
  "rg ", "rg", "grep ", "find ", "ls", "dir", "pwd", "cat ", "type ",
  "Get-Content", "Get-ChildItem", "Select-String", "Test-Path", "Measure-Object",
  // EOS state tools write only under .agents/evidence or .agents/registry. They
  // are the mechanism that authorises a project write, so blocking them would
  // make the guard self-locking (observed and fixed on 2026-11-20).
  // Tools that accept an arbitrary output path (impact --out, pytest_delta
  // --baseline, team run, model_probe invoke) are deliberately NOT in this list:
  // an allowlisted tool that can write anywhere is a bypass.
  "python .agents/tools/decision.py",
  "python .agents/tools/auto_decision.py",
  "python .agents/tools/project_map.py",
  "python .agents/tools/skill_usage.py",
  "python .agents/tools/recovery.py",
  "python .agents/tools/work_state.py",
  "python .agents/tools/preflight.py",
  "python .agents/tools/post_write.py",
  "python .agents/tools/write_gate.py",
  "python .agents/tools/plugin_install.py",
  "python engineering-os/engine/tools/plugin_install.py",
  "python .agents/tools/challenge.py",
  "python .agents/tools/quality_gate.py",
  "python .agents/tools/impact.py",
  "python .agents/tools/team.py",
  "python .agents/tools/workspace.py",
  "python .agents/tools/tool_evidence.py",
  "python .agents/tools/completion.py",
  "python .agents/tools/ship.py",
  "python .agents/tools/model_probe.py",
  "python .agents/tools/scope_install.py",
  "python .agents/router/route_task.py",
  "python .agents/tools/secret_scan.py",
  "python .agents/tools/graph_freshness.py",
  "python .agents/tools/route_task.py",
  "python .agents/tools/skill_index.py",
  "python .agents/tools/eos_activate.py",
  "python .agents/tools/runtime_permissions.py",
  "node --test", "node --version", "cargo test", "cargo build",
]

// Allowed prefixes that still have a forbidden argument. `impact.py --out`
// can overwrite any file; `graph_freshness.py invalidate` deletes the cache.
const DENIED_ARGS = [
  { prefix: "python .agents/tools/impact.py", token: "--out" },
  { prefix: "python .agents/tools/graph_freshness.py", token: "invalidate" },
  { prefix: "python .agents/tools/team.py", token: " run --plan" },
  { prefix: "python .agents/tools/scope_install.py", token: "scope_install.py install" },
  { prefix: "python .agents/tools/scope_install.py", token: "scope_install.py uninstall" },
  { prefix: "python .agents/tools/workspace.py", token: "scope set" },
]

const WRITE_PATTERNS = [
  /(?<![-=])>{1,2}(?![>=&])/,      // redirection, but not ->, =>, 2>&1 or >>
  /\bSet-Content\b/i, /\bAdd-Content\b/i, /\bOut-File\b/i, /\bNew-Item\b/i,
  /\bRemove-Item\b/i, /\bMove-Item\b/i, /\bCopy-Item\b/i, /\bRename-Item\b/i,
  /\bdel\s+/i, /\brm\s+/i, /\bmv\s+/i, /\bcp\s+/i, /\btee\b/i, /\bsed\s+-i\b/i,
  /\bgit\s+(add|commit|push|reset|checkout|clean|stash|merge|rebase)\b/i,
  /\bpip\s+install\b/i, /\bnpm\s+(install|uninstall|run\s+build)\b/i,
  /\bpython\s+-c\b/i, /\bpython\s+-m\s+pip\b/i, /\bnode\s+-e\b/i,
  /\bpowershell(\.exe)?\b/i, /\bcmd(\.exe)?\s+\/[ck]\b/i,
  /\bInvoke-Expression\b/i, /\biex\b/i,
]

function sha256(text) {
  return createHash("sha256").update(String(text)).digest("hex").toUpperCase()
}

function now() {
  return new Date().toISOString()
}

function workspaceOf(ctx, fallback) {
  const directory = ctx && ctx.location && ctx.location.directory
  return directory ? resolve(directory) : resolve(fallback || process.cwd())
}

function engineFor(workspace) {
  // Workspace layout wins: it is the tree the user is actually working in.
  const local = join(workspace, ".agents", "tools", "decision.py")
  if (existsSync(local)) {
    return { engine: join(workspace, ".agents"), python: process.env.EOS_PYTHON || "python" }
  }
  const home = process.env.EOS_HOME || join(process.env.USERPROFILE || process.env.HOME || "", ".eos")
  const versions = join(home, "versions")
  let newest = ""
  try {
    for (const name of readdirSync(versions)) if (name > newest) newest = name
  } catch {
    // no versions directory
  }
  if (newest) {
    const runtime = join(versions, newest, "runtime", "python",
      process.platform === "win32" ? "python.exe" : "bin/python3")
    return { engine: join(versions, newest, "engine"), python: runtime }
  }
  return { engine: join(home, "engine"), python: process.env.EOS_PYTHON || "python" }
}

function pythonArgs(engine, workspace, session) {
  const args = [join(engine, "tools", "decision.py"), "require-edit", "--json",
    "--workspace", workspace]
  if (session) args.push("--session", String(session))
  return args
}

/**
 * Run `decision.py require-edit` and read the last JSON line.
 * A missing engine, a crash or a timeout is DENY, never ALLOW: the guard must
 * fail closed.
 */
function askEngine(engine, python, workspace, path, session, timeoutMs = TIMEOUT_MS) {
  return new Promise((resolvePromise) => {
    let settled = false
    const done = (value) => {
      if (!settled) {
        settled = true
        resolvePromise(value)
      }
    }
    let child
    try {
      child = spawn(python, [...pythonArgs(engine, workspace, session), "--path", path], {
        cwd: workspace, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
      })
    } catch (error) {
      done({ ok: false, reasons: [`engine spawn failed: ${error.message}`] })
      return
    }
    let out = ""
    let err = ""
    const timer = setTimeout(() => {
      try { child.kill() } catch {}
      done({ ok: false, reasons: [`decision engine timed out after ${timeoutMs}ms`] })
    }, timeoutMs)
    child.stdout.on("data", (chunk) => { if (out.length < 100000) out += chunk.toString() })
    child.stderr.on("data", (chunk) => { if (err.length < 4000) err += chunk.toString() })
    child.on("error", (error) => {
      clearTimeout(timer)
      done({ ok: false, reasons: [`engine error: ${error.message}`] })
    })
    child.on("close", (code) => {
      clearTimeout(timer)
      const lines = out.trim().split(/\r?\n/).filter(Boolean)
      for (let index = lines.length - 1; index >= 0; index -= 1) {
        try {
          const parsed = JSON.parse(lines[index])
          if (parsed && typeof parsed.ok === "boolean") {
            done({ ...parsed, exit_code: code })
            return
          }
        } catch {
          // not the JSON line
        }
      }
      done({ ok: false, reasons: [`engine produced no decision (exit ${code}) ${err}`.slice(0, MAX_REASON)] })
    })
  })
}

function sessionKey(input) {
  const id = input && typeof input.sessionID === "string" ? input.sessionID : ""
  return id || "default"
}

/**
 * Prepare an Engineering Decision Record for one path, automatically.
 * Only ATOMIC is auto-approved by the engine; STANDARD returns approved=false
 * and CRITICAL always requires a human. The guard never invents an approval.
 */
function askAutoDecision(engine, python, workspace, task, path, session, timeoutMs = 12000) {
  return new Promise((resolvePromise) => {
    let settled = false
    const done = (value) => {
      if (!settled) {
        settled = true
        resolvePromise(value)
      }
    }
    let child
    try {
      child = spawn(python, [
        join(engine, "tools", "auto_decision.py"), "prepare",
        "--workspace", workspace, "--task", String(task).slice(0, 4000),
        "--scope", path, "--session", String(session || ""), "--approve", "--json",
      ], { cwd: workspace, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] })
    } catch (error) {
      done({ ok: false, reasons: [`auto decision spawn failed: ${error.message}`] })
      return
    }
    let out = ""
    let err = ""
    const timer = setTimeout(() => {
      try { child.kill() } catch {}
      done({ ok: false, reasons: ["auto decision timed out"] })
    }, timeoutMs)
    child.stdout.on("data", (chunk) => { if (out.length < 100000) out += chunk.toString() })
    child.stderr.on("data", (chunk) => { if (err.length < 4000) err += chunk.toString() })
    child.on("error", (error) => {
      clearTimeout(timer)
      done({ ok: false, reasons: [`auto decision error: ${error.message}`] })
    })
    child.on("close", () => {
      clearTimeout(timer)
      const lines = out.trim().split(/\r?\n/).filter(Boolean)
      for (let index = lines.length - 1; index >= 0; index -= 1) {
        try {
          const parsed = JSON.parse(lines[index])
          if (parsed && typeof parsed.approved === "boolean") {
            done({ ok: parsed.approved, decision: parsed.decision, risk: parsed.risk,
                   reasons: parsed.reasons || [], next: parsed.next || "",
                   state: parsed.state })
            return
          }
        } catch {
          // not the JSON line
        }
      }
      done({ ok: false, reasons: [`auto decision produced no JSON ${err}`.slice(0, MAX_REASON)] })
    })
  })
}

function askWriteGate(engine, python, workspace, path, session, timeoutMs = 12000) {
  return new Promise((resolvePromise) => {
    let settled = false
    const done = (value) => {
      if (!settled) {
        settled = true
        resolvePromise(value)
      }
    }
    let child
    try {
      child = spawn(python, [
        join(engine, "tools", "write_gate.py"), "check",
        "--workspace", workspace, "--path", path,
        "--session", String(session || ""), "--json",
      ], { cwd: workspace, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] })
    } catch (error) {
      done({ allow: false, needs_decision: false, reasons: [`write gate spawn failed: ${error.message}`] })
      return
    }
    let out = ""
    let err = ""
    const timer = setTimeout(() => {
      try { child.kill() } catch {}
      done({ allow: false, needs_decision: false, reasons: ["write gate timed out"] })
    }, timeoutMs)
    child.stdout.on("data", (chunk) => { if (out.length < 100000) out += chunk.toString() })
    child.stderr.on("data", (chunk) => { if (err.length < 4000) err += chunk.toString() })
    child.on("error", (error) => {
      clearTimeout(timer)
      done({ allow: false, needs_decision: false, reasons: [`write gate error: ${error.message}`] })
    })
    child.on("close", () => {
      clearTimeout(timer)
      const lines = out.trim().split(/\r?\n/).filter(Boolean)
      for (let index = lines.length - 1; index >= 0; index -= 1) {
        try {
          const parsed = JSON.parse(lines[index])
          if (parsed && typeof parsed.allow === "boolean") {
            done({ ...parsed, reasons: parsed.reasons || [] })
            return
          }
        } catch {
          // not the JSON line
        }
      }
      done({ allow: false, needs_decision: false,
             reasons: [`write gate produced no JSON ${err}`.slice(0, MAX_REASON)] })
    })
  })
}

function runTool(engine, python, workspace, script, args, timeoutMs = TIMEOUT_MS) {
  return new Promise((resolvePromise) => {
    let settled = false
    const done = (value) => {
      if (!settled) {
        settled = true
        resolvePromise(value)
      }
    }
    let child
    try {
      child = spawn(python, [join(engine, "tools", script), ...args],
        { cwd: workspace, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] })
    } catch (error) {
      done({ ok: false, reason: `${script} spawn failed: ${error.message}` })
      return
    }
    let out = ""
    let err = ""
    const timer = setTimeout(() => {
      try { child.kill() } catch {}
      done({ ok: false, reason: `${script} timed out` })
    }, timeoutMs)
    child.stdout.on("data", (chunk) => { if (out.length < 100000) out += chunk.toString() })
    child.stderr.on("data", (chunk) => { if (err.length < 4000) err += chunk.toString() })
    child.on("error", (error) => {
      clearTimeout(timer)
      done({ ok: false, reason: `${script} error: ${error.message}` })
    })
    child.on("close", (code) => {
      clearTimeout(timer)
      done({ ok: code === 0, code, out: out.slice(0, 2000), reason: err.slice(0, MAX_REASON) })
    })
  })
}

function recordBlocked(engine, python, workspace, session, path, reasons, decision,
                       mapState, mapSha) {
  const reason = (Array.isArray(reasons) ? reasons.join("; ") : String(reasons || ""))
    .slice(0, 2000)
  return runTool(engine, python, workspace, "work_state.py", [
    "record-blocked", "--workspace", workspace, "--path", path,
    "--session", String(session || ""), "--reason", reason || "write denied",
    "--decision", String(decision || ""), "--map-state", String(mapState || ""),
    "--map-sha", String(mapSha || ""), "--json",
  ])
}

function recordChange(engine, python, workspace, decision, path, session) {
  return runTool(engine, python, workspace, "decision.py", [
    "record-change", "--workspace", workspace, "--id", String(decision),
    "--file", path, "--session", String(session || ""), "--json",
  ])
}

function touchWrite(engine, python, workspace, path, session, timeoutMs = TIMEOUT_MS) {
  // The post-write must be awaited: the next gate check has to see the map the
  // write produced, not the map from before it (0.3.4).
  return new Promise((resolvePromise) => {
    let settled = false
    let child
    const done = (value) => {
      if (!settled) { settled = true; resolvePromise(value) }
    }
    try {
      child = spawn(python, [
        join(engine, "tools", "post_write.py"), "touch", "--json",
        "--workspace", workspace, "--path", path, "--session", String(session || ""),
      ], { cwd: workspace, windowsHide: true })
    } catch (error) {
      done({ ok: false, reason: `post-write failed: ${error.message}` })
      return
    }
    const timer = setTimeout(() => {
      done({ ok: false, reason: "post-write timeout" })
      try { child.kill() } catch { /* already gone */ }
    }, timeoutMs)
    let out = ""
    child.stdout.on("data", (chunk) => { if (out.length < 100000) out += chunk.toString() })
    child.on("error", (error) => {
      clearTimeout(timer)
      done({ ok: false, reason: `post-write failed: ${error.message}` })
    })
    child.on("close", (code) => {
      clearTimeout(timer)
      const lines = out.trim().split(/\r?\n/).filter(Boolean)
      for (let index = lines.length - 1; index >= 0; index -= 1) {
        try {
          const parsed = JSON.parse(lines[index])
          if (parsed && typeof parsed.ok === "boolean") {
            done({ ...parsed, ok: code === 0 && parsed.ok === true })
            return
          }
        } catch {
          // not the JSON line
        }
      }
      done({ ok: false, reason: `post-write produced no JSON (rc=${code})` })
    })
  })
}

/**
 * Record a completed ATOMIC write as closed work-state history.
 *
 * This runs only after `post_write` succeeded. It never starts an open task, so
 * a second session is not locked out; work_state.py owns the OS lock and the
 * idempotency check. A failure here is evidence, not a failed write.
 */
function recordAtomic(engine, python, workspace, pending, touched, session,
                      timeoutMs = 8000) {
  if (!pending || !pending.decision || pending.risk !== "ATOMIC") {
    return Promise.resolve({ ok: true, status: "skipped",
                             reason: "not an ATOMIC decision" })
  }
  if (!touched || !touched.ok || !touched.project) {
    return Promise.resolve({ ok: false, status: "failed",
                             reason: "post-write did not produce a project" })
  }
  return new Promise((resolvePromise) => {
    let settled = false
    let child
    const done = (value) => {
      if (!settled) { settled = true; resolvePromise(value) }
    }
    try {
      child = spawn(python, [
        join(engine, "tools", "work_state.py"), "record-atomic", "--json",
        "--workspace", workspace, "--project", touched.project,
        "--task", String(pending.task || "").slice(0, 4000),
        "--session", String(session || ""), "--decision", String(pending.decision),
        "--file", String(pending.path || ""),
        "--map-state", String(touched.map_state || ""),
      ], { cwd: workspace, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] })
    } catch (error) {
      done({ ok: false, status: "failed", reason: `work-state spawn failed: ${error.message}` })
      return
    }
    let out = ""
    let err = ""
    const timer = setTimeout(() => {
      try { child.kill() } catch {}
      done({ ok: false, status: "failed", reason: "work-state record timed out" })
    }, timeoutMs)
    child.stdout.on("data", (chunk) => { if (out.length < 100000) out += chunk.toString() })
    child.stderr.on("data", (chunk) => { if (err.length < 4000) err += chunk.toString() })
    child.on("error", (error) => {
      clearTimeout(timer)
      done({ ok: false, status: "failed", reason: `work-state error: ${error.message}` })
    })
    child.on("close", (code) => {
      clearTimeout(timer)
      const lines = out.trim().split(/\r?\n/).filter(Boolean)
      for (let index = lines.length - 1; index >= 0; index -= 1) {
        try {
          const parsed = JSON.parse(lines[index])
          if (parsed && typeof parsed.ok === "boolean") {
            done({ ...parsed, ok: code === 0 && parsed.ok === true })
            return
          }
        } catch {
          // not the JSON line
        }
      }
      done({ ok: false, status: "failed",
             reason: `work-state produced no JSON (rc=${code}) ${err}`.slice(0, MAX_REASON) })
    })
  })
}

function toolFailed(output) {
  // The host may report a failed tool either as an object or as text. A write
  // that did not succeed must not touch the map or create a closed work state.
  const value = output || {}
  if (value.error) return true
  const text = typeof value.output === "string" ? value.output.trim() : ""
  return /^(error\b|failed\b|permission\.rejected\b)/i.test(text)
}

function writeTarget(tool, args) {
  const value = args || {}
  const candidate = value.filePath ?? value.file_path ?? value.path ?? value.file ??
    value.target ?? value.filename
  if (typeof candidate === "string" && candidate.trim()) return candidate.trim()
  return ""
}

function shellCommand(args) {
  const value = args || {}
  const candidate = value.command ?? value.cmd ?? value.script
  if (typeof candidate === "string") return candidate
  if (Array.isArray(candidate)) return candidate.join(" ")
  return ""
}

const SAFE_READ_VERBS = new Set([
  "echo", "printf", "pwd", "ls", "dir", "cat", "type", "head", "tail", "wc",
  "find", "findstr", "rg", "grep", "where", "which", "whoami", "hostname",
  "date", "sort", "uniq", "diff", "cmp", "file", "stat", "du", "df", "env",
  "printenv", "uname", "true", "false", "ping", "test",
])

const SAFE_PS_VERBS = new Set([
  "get-content", "get-childitem", "get-item", "get-command", "get-location",
  "get-date", "get-process", "get-service", "get-help", "get-member",
  "get-filehash", "get-acl", "get-itemproperty", "get-volume", "get-psdrive",
  "select-string", "select-object", "where-object", "foreach-object",
  "test-path", "measure-object", "resolve-path", "split-path", "join-path",
  "format-table", "format-list", "format-hex", "out-string", "sort-object",
  "write-output", "write-host", "start-sleep", "test-connection",
])

const WRITE_VERBS = new Set([
  "set-content", "add-content", "out-file", "new-item", "remove-item",
  "move-item", "copy-item", "rename-item", "clear-content", "set-item",
  "set-itemproperty", "new-itemproperty", "remove-itemproperty", "tee-object",
  "del", "erase", "rm", "rmdir", "rd", "mv", "move", "cp", "copy", "mkdir",
  "md", "tee", "touch", "truncate", "chmod", "chown", "icacls", "attrib",
  "cacls", "takeown",
])

const DESTRUCTIVE_VERBS = new Set([
  "remove-item", "del", "erase", "rm", "rmdir", "rd", "move-item", "mv",
  "move", "rename-item", "ren", "clear-content", "set-item", "set-itemproperty",
  "remove-itemproperty", "chmod", "chown", "icacls", "attrib", "cacls",
  "takeown", "truncate",
])

const SAFE_GIT = new Set([
  "status", "diff", "log", "show", "rev-parse", "ls-files", "describe",
  "blame", "shortlog", "reflog",
])

const RISKY_GIT = new Set([
  "add", "commit", "push", "fetch", "pull", "checkout", "switch", "restore",
  "merge", "rebase", "cherry-pick", "revert", "stash", "tag", "rm", "mv",
  "apply", "am", "format-patch",
])

const DENY_GIT = new Set(["reset", "clean", "filter-branch", "gc"])

const NETWORK_VERBS = new Set([
  "curl", "wget", "iwr", "irm", "invoke-webrequest", "invoke-restmethod",
  "ssh", "scp", "sftp", "ftp", "nc", "netcat", "telnet", "winget", "choco",
  "scoop", "pip", "pip3",
])

const EXECUTABLE_RE = /\.(?:exe|bat|cmd|ps1|com)$/i

const PROTECTED_RE =
  /(^|[/\\])(?:\.opencode[/\\](?:plugin|plugins)|\.agents[/\\](?:tools|registry|core)|eos-guard\.js|eos-activation\.js|GATES\.json|PROJECT_PROFILES\.json|write_gate\.py|post_write\.py|work_state\.py|decision\.py|auto_decision\.py|ship\.py|plugin_install\.py|scope_install\.py)/i

function shellVerdict(effect, reason, targets = [], extra = {}) {
  return {
    effect,
    allow: effect === "allow",
    ask: effect === "ask",
    deny: effect === "deny",
    reason,
    targets: [...new Set((targets || []).filter(Boolean))],
    ...extra,
  }
}

function unquote(value) {
  const text = String(value || "")
  if ((text.startsWith('"') && text.endsWith('"')) ||
      (text.startsWith("'") && text.endsWith("'"))) {
    return text.slice(1, -1)
  }
  return text
}

function basenameWord(token) {
  return unquote(token).replace(/\\/g, "/").split("/").pop().toLowerCase()
}

function verbName(token) {
  let name = basenameWord(token).replace(/^@/, "")
  for (const ext of [".exe", ".cmd", ".bat", ".ps1", ".com"]) {
    if (name.endsWith(ext)) {
      name = name.slice(0, -ext.length)
      break
    }
  }
  return name
}

function splitTokens(text) {
  const out = []
  let current = ""
  let quote = ""
  for (let index = 0; index < text.length; index += 1) {
    const ch = text[index]
    if (quote) {
      current += ch
      if (ch === quote && text[index - 1] !== "\\") quote = ""
      continue
    }
    if (ch === '"' || ch === "'") {
      quote = ch
      current += ch
      continue
    }
    if (/\s/.test(ch)) {
      if (current) {
        out.push(current)
        current = ""
      }
      continue
    }
    current += ch
  }
  if (current) out.push(current)
  return out
}

function splitShell(text) {
  const parts = []
  let current = ""
  let quote = ""
  for (let index = 0; index < text.length; index += 1) {
    const ch = text[index]
    if (quote) {
      current += ch
      if (ch === quote && text[index - 1] !== "\\") quote = ""
      continue
    }
    if (ch === '"' || ch === "'") {
      quote = ch
      current += ch
      continue
    }
    if (ch === "&" && text[index + 1] === "&") {
      parts.push(current)
      current = ""
      index += 1
      continue
    }
    if (ch === "|" && text[index + 1] === "|") {
      parts.push(current)
      current = ""
      index += 1
      continue
    }
    if (ch === ";" || ch === "|") {
      parts.push(current)
      current = ""
      continue
    }
    if (ch === "&" && text[index + 1] !== "&" &&
        text[index - 1] !== ">" && text[index - 1] !== "<") {
      parts.push(current)
      current = ""
      continue
    }
    current += ch
  }
  parts.push(current)
  return parts.map((part) => part.trim()).filter(Boolean)
}

function unwrapShell(segment) {
  let text = segment.trim().replace(/^&\s*/, "")
  let opaque = false
  for (let depth = 0; depth < 4; depth += 1) {
    const words = splitTokens(text)
    if (!words.length) break
    const head = basenameWord(words[0])
    if (!["cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
      "bash", "bash.exe", "sh", "sh.exe"].includes(head)) break
    const index = words.findIndex((word, position) => position > 0 &&
      (/^\/(c|k)$/i.test(unquote(word)) ||
       /^-(c|command|encodedcommand)$/i.test(unquote(word))))
    if (index < 0) break
    const flag = unquote(words[index]).toLowerCase()
    if (flag === "-encodedcommand" || flag === "-e") {
      opaque = true
      break
    }
    const inner = words.slice(index + 1).map(unquote).join(" ").trim()
    if (!inner) break
    text = inner
  }
  return { text, opaque }
}

function operatorText(text) {
  return String(text || "").replace(/"[^"]*"|'[^']*'/g, '""')
}

function findRedirection(text) {
  const match = operatorText(text).match(
    /(^|[^<>=])(>>?)(?![>=&])\s*([^\s|;&]+)?/,
  )
  return match && match[3] ? unquote(match[3]) : ""
}

function protectedPath(target) {
  return PROTECTED_RE.test(String(target || "").replace(/\\/g, "/"))
}

function insideWorkspace(workspace, target) {
  if (!workspace) return true
  if (!target || String(target).startsWith("~")) return false
  const root = resolve(workspace)
  const absolute = resolve(workspace, unquote(target))
  return absolute === root || absolute.startsWith(root + sep)
}

function optionValue(args, names) {
  for (let index = 0; index < args.length; index += 1) {
    const value = unquote(args[index])
    const lower = value.toLowerCase()
    if (names.includes(lower) && index + 1 < args.length) {
      return unquote(args[index + 1])
    }
    for (const name of names) {
      if (lower.startsWith(name + ":") || lower.startsWith(name + "=")) {
        return value.slice(name.length + 1)
      }
    }
  }
  return ""
}

function positionals(args) {
  return args.map(unquote).filter((value) => value && !value.startsWith("-"))
}

function targetsForVerb(verb, args) {
  const values = args.map(unquote)
  const first = positionals(values)[0] || ""
  const second = positionals(values)[1] || ""
  if (["set-content", "add-content", "out-file", "new-item", "remove-item",
    "clear-content", "set-item", "set-itemproperty", "new-itemproperty",
    "remove-itemproperty", "tee-object", "tee", "touch", "truncate", "mkdir",
    "md"].includes(verb)) {
    const option = optionValue(values, ["-path", "-filepath", "-literalpath",
      "--file", "--path"])
    if (option) return [option]
    if (verb === "tee" || verb === "tee-object") return [first]
    if (verb === "touch") return positionals(values).slice(0, 1)
    return [first]
  }
  if (["move-item", "move", "mv", "rename-item", "ren"].includes(verb)) {
    return positionals(values).slice(0, 2)
  }
  if (["copy-item", "copy", "cp"].includes(verb)) {
    return [second || first]
  }
  if (["del", "erase", "rm", "rmdir", "rd"].includes(verb)) {
    return positionals(values).slice(0, 1)
  }
  if (["chmod", "chown", "icacls", "attrib", "cacls", "takeown"].includes(verb)) {
    return positionals(values).slice(0, 1)
  }
  return []
}

function targetVerdict(targets, workspace, reason, destructive = false) {
  const list = [...new Set((targets || []).filter(Boolean))]
  for (const target of list) {
    if (protectedPath(target)) {
      return shellVerdict("deny", `protected EOS path: ${target}`, list)
    }
  }
  if (!list.length) {
    return shellVerdict("ask", `${reason}: target is not inspectable`, [])
  }
  if (list.some((target) => !insideWorkspace(workspace, target))) {
    return shellVerdict("ask",
      `${reason}: target is outside the workspace (explicit approval required)`, list)
  }
  return shellVerdict("ask",
    `${reason}: ${list[0]} requires approval or an approved EDR`, list,
    { destructive })
}

function deniedArgument(lower) {
  for (const rule of DENIED_ARGS) {
    if ((lower === rule.prefix || lower.startsWith(rule.prefix + " ")) &&
        lower.includes(rule.token)) {
      return rule
    }
  }
  return null
}

function trustedPrefix(lower) {
  for (const prefix of READ_ONLY_PREFIXES) {
    const value = prefix.toLowerCase()
    if (lower === value || lower.startsWith(value + " ")) return prefix
  }
  return ""
}

function scriptBlockVerdict(text, workspace) {
  const match = text.match(/\{([\s\S]*)\}/)
  if (!match) return null
  const block = match[1].replace(/"[^"]*"|'[^']*'/g, "")
  const hit = block.match(
    /\b(Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Move-Item|Copy-Item|Rename-Item|del|rm|mv|cp|tee|git\s+(?:add|commit|push|reset|clean|checkout|stash|merge|rebase)|Invoke-Expression|iex)\b/i,
  )
  if (!hit) return null
  if (protectedPath(block)) {
    return shellVerdict("deny", "protected EOS path inside a script block", [])
  }
  return shellVerdict("ask",
    `script block contains ${hit[1]} and requires approval`, [])
}

function gitVerdict(args, workspace) {
  const values = args.map(unquote)
  const parts = positionals(values).map((value) => value.toLowerCase())
  const sub = parts[0] || ""
  const raw = values.join(" ")
  if (SAFE_GIT.has(sub)) return shellVerdict("allow", `read-only git ${sub}`)
  if (sub === "branch") {
    if (values.some((value) => /^(-d|-D|--delete|-m|-M|--move)/.test(value))) {
      return shellVerdict("deny", "deleting or moving a branch is not allowed")
    }
    return shellVerdict("allow", "read-only git branch")
  }
  if (sub === "remote") {
    if (!values.length || values.some((value) =>
      /^(-v|--verbose|show|get-url)$/i.test(value))) {
      return shellVerdict("allow", "read-only git remote")
    }
    return shellVerdict("ask", "changing a git remote requires approval")
  }
  if (sub === "tag") {
    if (!values.length || values.some((value) => /^(-l|--list|-n)/i.test(value))) {
      return shellVerdict("allow", "read-only git tag")
    }
    if (values.some((value) => /^(-d|--delete)/i.test(value))) {
      return shellVerdict("deny", "deleting a git tag is not allowed")
    }
    return shellVerdict("ask", "creating a git tag requires approval")
  }
  if (sub === "config") {
    if (values.some((value) => /^(--get|--list|-l|--get-all|--show-origin)/i.test(value))) {
      return shellVerdict("allow", "read-only git config")
    }
    return shellVerdict("ask", "changing git config requires approval")
  }
  if (sub === "stash" && ["list", "show"].includes(parts[1] || "")) {
    return shellVerdict("allow", "read-only git stash")
  }
  if (sub === "push" && values.some((value) => /^(--force|-f)$/i.test(value))) {
    return shellVerdict("deny", "force-push is not allowed")
  }
  if (RISKY_GIT.has(sub)) {
    if (protectedPath(raw)) {
      return shellVerdict("deny", "protected EOS path in a git command")
    }
    return shellVerdict("ask", `git ${sub} changes repository state; approval required`)
  }
  if (DENY_GIT.has(sub)) return shellVerdict("deny", `git ${sub} is destructive`)
  return shellVerdict("ask", `git ${sub || "command"} requires approval`)
}

function pythonVerdict(args) {
  const values = args.map(unquote)
  if (!values.length) return shellVerdict("ask", "python without a script requires approval")
  if (["--version", "-V"].includes(values[0])) return shellVerdict("allow", "python version")
  if (values[0] === "-m" && ["pytest", "unittest"].includes((values[1] || "").toLowerCase())) {
    return shellVerdict("allow", "python test runner")
  }
  if (values[0] === "-m" && (values[1] || "").toLowerCase() === "pip") {
    return shellVerdict("ask", "package installation requires approval")
  }
  if (values[0] === "-c") return shellVerdict("deny", "inline code is not inspectable")
  if (values[0] === "-") return shellVerdict("deny", "stdin code is not inspectable")
  if (/[/\\]engine[/\\]tools[/\\](decision|auto_decision|project_map|recovery|skill_usage|write_gate|post_write|work_state)\.py$/i.test(values[0])) {
    return shellVerdict("allow", "EOS engine tool")
  }
  if (/\.py$/i.test(values[0])) {
    return shellVerdict("ask", `python script requires approval: ${values[0]}`)
  }
  return shellVerdict("ask", "python invocation requires approval")
}

function nodeVerdict(args) {
  const values = args.map(unquote)
  if (!values.length) return shellVerdict("ask", "node without a script requires approval")
  if (["--version", "-v"].includes(values[0])) return shellVerdict("allow", "node version")
  if (values[0] === "--test") return shellVerdict("allow", "node test runner")
  if (values[0] === "-e" || values[0] === "--eval") {
    return shellVerdict("deny", "inline code is not inspectable")
  }
  return shellVerdict("ask", "node script requires approval")
}

function npmVerdict(args) {
  const values = args.map(unquote)
  const sub = (values[0] || "").toLowerCase()
  if (!values.length || ["--version", "-v"].includes(sub)) {
    return shellVerdict("allow", "npm version")
  }
  if (sub === "test") return shellVerdict("allow", "npm test")
  if (sub === "run") {
    const script = (values[1] || "").toLowerCase()
    if (["test", "build", "lint", "check"].includes(script)) {
      return shellVerdict("allow", `npm run ${script}`)
    }
    return shellVerdict("ask", `npm run ${script || "script"} requires approval`)
  }
  if (["install", "i", "ci", "uninstall", "update"].includes(sub)) {
    return shellVerdict("ask", "package installation requires approval")
  }
  return shellVerdict("ask", `npm ${sub || "command"} requires approval`)
}

function cargoVerdict(args) {
  const values = args.map(unquote)
  const sub = (values[0] || "").toLowerCase()
  if (!values.length) return shellVerdict("ask", "cargo command requires approval")
  if (["test", "build", "check", "clippy"].includes(sub)) {
    return shellVerdict("allow", `cargo ${sub}`)
  }
  if (["publish", "install", "login"].includes(sub)) {
    return shellVerdict("deny", `cargo ${sub} is not allowed`)
  }
  return shellVerdict("ask", `cargo ${sub} requires approval`)
}

function goVerdict(args) {
  const values = args.map(unquote)
  const sub = (values[0] || "").toLowerCase()
  if (["test", "build", "vet"].includes(sub)) return shellVerdict("allow", `go ${sub}`)
  return shellVerdict("ask", `go ${sub || "command"} requires approval`)
}

function dotnetVerdict(args) {
  const values = args.map(unquote)
  const sub = (values[0] || "").toLowerCase()
  if (["test", "build"].includes(sub)) return shellVerdict("allow", `dotnet ${sub}`)
  return shellVerdict("ask", `dotnet ${sub || "command"} requires approval`)
}

function analyzeSegment(segment, workspace) {
  const { text, opaque } = unwrapShell(segment)
  if (opaque) return shellVerdict("deny", "encoded command is not inspectable")
  if (!text) return shellVerdict("deny", "empty shell command")
  const singleQuoted = text.replace(/'[^']*'/g, "''")
  if (/`|\$\(/.test(singleQuoted)) {
    return shellVerdict("deny", "command substitution is not inspectable")
  }
  const lower = text.toLowerCase()
  if (/\b(runas|sudo|gsudo)\b/.test(lower) ||
      /start-process[^;|]*\b-verb\s+runas\b/.test(lower)) {
    return shellVerdict("ask", "privilege elevation requires explicit approval")
  }
  const redirect = findRedirection(text)
  const words = splitTokens(text)
  if (!words.length) return shellVerdict("deny", "empty shell command")
  const rawHead = unquote(words[0])
  const head = verbName(rawHead)
  const args = words.slice(1)
  const normalized = lower.replace(/\\/g, "/")
  const denied = deniedArgument(normalized)
  if (denied) {
    return shellVerdict("deny",
      `forbidden argument ${denied.token} for ${denied.prefix}`)
  }
  const trusted = trustedPrefix(normalized)
  if (trusted) {
    if (redirect) return targetVerdict([redirect], workspace, "redirection")
    return shellVerdict("allow", `read-only prefix: ${trusted}`)
  }
  if (lower.startsWith("eos install") && lower.includes(".agents")) {
    if (redirect) return targetVerdict([redirect], workspace, "redirection")
    return shellVerdict("allow", "trusted local installer into .agents")
  }
  if (lower.startsWith("eos install")) {
    return shellVerdict("deny", "installer target must be .agents")
  }
  const block = scriptBlockVerdict(text, workspace)
  if (block) return block
  if (redirect) return targetVerdict([redirect], workspace, "redirection")
  if (WRITE_VERBS.has(head)) {
    return targetVerdict(targetsForVerb(head, args), workspace,
      `${head} changes files`, DESTRUCTIVE_VERBS.has(head))
  }
  if (head === "git") return gitVerdict(args, workspace)
  if (["python", "python3", "py"].includes(head)) return pythonVerdict(args)
  if (head === "node") return nodeVerdict(args)
  if (head === "npm") return npmVerdict(args)
  if (head === "npx") return shellVerdict("ask", "npx can download and run an arbitrary package")
  if (head === "cargo") return cargoVerdict(args)
  if (head === "go") return goVerdict(args)
  if (head === "dotnet") return dotnetVerdict(args)
  if (["pip", "pip3"].includes(head)) {
    return shellVerdict("ask", "package installation requires approval")
  }
  if (["pytest", "py.test"].includes(head)) return shellVerdict("allow", "test command")
  if (head === "sed") {
    if (args.some((value) => /^(-i|--in-place)/i.test(unquote(value)))) {
      const target = positionals(args).slice(-1)[0] || ""
      return targetVerdict([target], workspace, "in-place edit")
    }
    return shellVerdict("allow", "sed read")
  }
  if (SAFE_READ_VERBS.has(head)) return shellVerdict("allow", `read-only command: ${head}`)
  if (SAFE_PS_VERBS.has(head)) return shellVerdict("allow", `read-only command: ${head}`)
  if (NETWORK_VERBS.has(head)) {
    return shellVerdict("ask", `network command requires approval: ${head}`)
  }
  if (["start", "start-process", "invoke-item", "invoke-command", "start-job",
    "measure-command"].includes(head)) {
    return shellVerdict("ask", `process execution requires approval: ${head}`)
  }
  if (["invoke-expression", "iex"].includes(head)) {
    return shellVerdict("deny", "dynamic code execution is not allowed")
  }
  if (["cmd", "powershell", "pwsh", "bash", "sh"].includes(head)) {
    return shellVerdict("ask",
      `${head} without an inspectable /c or -Command requires approval`)
  }
  if (EXECUTABLE_RE.test(rawHead)) {
    return shellVerdict("ask", `unknown executable requires approval: ${rawHead}`)
  }
  return shellVerdict("ask", `unclassified shell command requires approval: ${head}`)
}

/**
 * Classify a shell command into allow / ask / deny.
 *
 * Safe read-only commands and safe wrappers (`cmd /c echo`, `powershell
 * -Command Get-ChildItem`) are allowed. File changes are tied to the existing
 * write gate/EDR and otherwise ask. Unknown executables, elevation and opaque
 * code ask or deny. Quoted arguments are not scanned for write words; the old
 * scan blocked `rg "Set-Content"` and `echo "cmd /c echo"`.
 */
export function classifyShell(command, workspace = "") {
  const text = String(command || "").trim()
  if (!text) return shellVerdict("deny", "empty shell command")
  const singleQuoted = text.replace(/'[^']*'/g, "''")
  if (/`|\$\(/.test(singleQuoted)) {
    return shellVerdict("deny", "command substitution is not inspectable")
  }
  const parts = splitShell(text)
  if (!parts.length) return shellVerdict("deny", "empty shell command")
  let effect = "allow"
  const reasons = []
  const targets = []
  for (const part of parts) {
    const verdict = analyzeSegment(part, workspace)
    if (verdict.effect === "deny") return verdict
    if (verdict.effect === "ask") effect = "ask"
    reasons.push(verdict.reason)
    targets.push(...(verdict.targets || []))
  }
  return shellVerdict(effect, reasons.join("; "), targets)
}

const WRITE_ACTIONS = new Set(["write", "edit", "patch", "multiedit", "apply_patch"])
const SHELL_ACTIONS = new Set(["shell", "bash", "sh", "terminal"])

/**
 * The V2 permission input is the only place the host exposes the shell command:
 *
 *   { sessionID, agent, action: "shell",
 *     resources: ["Set-Content -Path x -Value OK"], effect: "allow" }
 *
 * `input.effect` accepts allow, ask and deny. The guard returns a matching
 * effect and the permission hook applies it.
 */
export function permissionDecision(input, workspace = "") {
  const action = String(input && input.action || "")
  const resources = Array.isArray(input && input.resources) ? input.resources.map(String) : []
  if (SHELL_ACTIONS.has(action)) {
    if (!resources.length) {
      return { kind: "shell", ...shellVerdict("deny", "shell permission carries no command") }
    }
    let effect = "allow"
    const reasons = []
    const targets = []
    let resource = ""
    for (const command of resources) {
      const verdict = classifyShell(command, workspace)
      if (verdict.effect === "deny") {
        return { kind: "shell", ...verdict, resource: command }
      }
      if (verdict.effect === "ask") effect = "ask"
      reasons.push(verdict.reason)
      targets.push(...(verdict.targets || []))
      if (!resource && verdict.effect !== "allow") resource = command
    }
    return {
      kind: "shell", ...shellVerdict(effect, reasons.join("; "), targets),
      resource: resource || resources.join(" && "),
    }
  }
  if (WRITE_ACTIONS.has(action)) {
    return { kind: "write", allow: null, resource: resources[0] || "", reason: "needs decision lookup" }
  }
  if (action === "read") return { kind: "read", allow: true, effect: "allow", resource: resources.join(","), reason: "read" }
  return { kind: action || "unknown", allow: true, effect: "allow", resource: resources.join(","), reason: "not a write surface" }
}

function evidencePath(workspace, sessionID) {
  const safe = String(sessionID || "default").replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 80)
  return join(workspace, ".agents", "evidence", "tools", `${safe}.jsonl`)
}

function record(workspace, sessionID, entry) {
  try {
    const path = evidencePath(workspace, sessionID)
    mkdirSync(dirname(path), { recursive: true })
    appendFileSync(path, JSON.stringify({ ts: now(), session: String(sessionID || ""), ...entry }) + "\n", "utf8")
  } catch {
    // Evidence loss must not crash the host, but it must not turn a deny into
    // an allow either: recording happens after the verdict is computed.
  }
}

export function callIDOf(input) {
  const value = input || {}
  return String(value.callID || value.callId || value.id || value.toolCallID || "")
}

export function summaryOf(tool, args) {
  const value = args || {}
  const keys = Object.keys(value).sort()
  const path = writeTarget(tool, value) || shellCommand(value)
  return {
    tool,
    keys,
    path: typeof path === "string" ? path.slice(0, 300) : "",
    bytes: typeof value.content === "string" ? value.content.length : 0,
    args_sha256: sha256(JSON.stringify(value)).slice(0, 32),
  }
}

export function skillFromPath(path, workspace) {
  const raw = String(path || "").trim()
  if (!raw) return null
  const absolute = isAbsolute(raw) ? resolve(raw) : resolve(workspace, raw)
  const root = resolve(workspace)
  const absoluteLower = absolute.toLowerCase()
  const rootLower = root.toLowerCase()
  if (!(absoluteLower === rootLower || absoluteLower.startsWith(rootLower + sep))) return null
  const relative = absolute.slice(root.length).replace(/^[/\\]+/, "").replace(/\\/g, "/")
  if (relative !== "SKILL.md" && !relative.endsWith("/SKILL.md")) return null
  if (!relative.includes(".agents/skills/") && !relative.includes(".opencode/skills/")) return null
  const parts = relative.split("/")
  if (parts.length < 3) return null
  const skill = parts[parts.length - 2]
  let digest = ""
  try {
    digest = sha256(readFileSync(absolute, "utf8"))
  } catch {
    // A read may be truncated by the host; the path and skill are still
    // evidence, the hash is simply absent.
  }
  return { skill, path: relative, sha256: digest }
}

export function readTarget(tool, args) {
  if (!READ_TOOLS.has(tool)) return ""
  const value = args || {}
  return String(value.path || value.filePath || value.file || value.filename || "")
}

export function skillFromReadOutput(output, workspace) {
  const text = typeof output === "string"
    ? output
    : (output && typeof output.output === "string" ? output.output : "")
  const match = text.match(/^Read file (.+?), lines \d+/m)
  if (!match) return null
  return skillFromPath(match[1].trim(), workspace)
}

function recordSkill(workspace, sessionID, info) {
  try {
    const session = String(sessionID || "unknown")
      .replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 80) || "unknown"
    const path = join(workspace, ".agents", "evidence", "skills", session + ".jsonl")
    mkdirSync(dirname(path), { recursive: true })
    appendFileSync(path, JSON.stringify({
      ts: now(), kind: "skill", verdict: "USED", ...info, source: "guard-read",
    }) + "\n", "utf8")
  } catch {
    // Evidence loss must not turn a read into a deny.
  }
}

export function createWriteQueue(ttlMs = 5 * 60 * 1000, limit = 64) {
  // A write is queued when the guard allows it and dequeued only after the host
  // reports that it executed. Marking the map before the write is a lie when
  // the write fails; that was the 0.3.3 defect this queue removes.
  //
  // Matching uses the host tool-call id when it is available. Same session and
  // same path can legitimately occur twice at once; without the id the second
  // completion would consume the first entry twice and leave a stale one.
  const items = []
  const fresh = (item) => Date.now() - item.at < ttlMs
  return {
    note(path, session, callID, meta) {
      items.push({ path, session, callID: String(callID || ""), ...(meta || {}),
                   at: Date.now() })
      while (items.length > limit) items.shift()
    },
    take(session, summary, callID) {
      const wanted = String(callID || "")
      if (wanted) {
        const exact = items.findIndex((item) => fresh(item) &&
          item.callID === wanted && item.session === session)
        if (exact >= 0) {
          const [found] = items.splice(exact, 1)
          return found
        }
        // OpenCode 2.3.2: the permission event carries the path but no call id;
        // the execute.after event carries a call id but no args. Pair the
        // anonymous entry in this session so the post-write refresh still runs.
        // The entry is consumed once and never crosses sessions.
        const anonymous = items.findIndex((item) => fresh(item) &&
          item.session === session && !item.callID)
        if (anonymous >= 0) {
          const [found] = items.splice(anonymous, 1)
          return found
        }
        return null
      }
      // Oldest-first: every completion consumes exactly one queued write.
      for (let index = 0; index < items.length; index += 1) {
        const item = items[index]
        if (!fresh(item)) { items.splice(index, 1); index -= 1; continue }
        if (item.session !== session) continue
        if (summary && summary.path && item.path !== summary.path) continue
        items.splice(index, 1)
        return item
      }
      return null
    },
    size() {
      for (let index = items.length - 1; index >= 0; index -= 1) {
        if (!fresh(items[index])) items.splice(index, 1)
      }
      return items.length
    },
  }
}

async function install(ctx, options) {
  const workspace = workspaceOf(ctx, options && options.workspace)
  const { engine, python } = engineFor(workspace)
  const cache = new Map()
  const sessionTasks = new Map()
  const pendingReads = new Map()
  const writes = createWriteQueue()
  const noteWrite = writes.note
  const takeWrite = writes.take
  let enforcement = "unknown"
  let hooks = []
  let permissionHookAvailable = false

  const taskFor = (sessionID) => sessionTasks.get(sessionKey({ sessionID })) || ""
  const allowAtomic = async (sessionID, path) => {
    const task = taskFor(sessionID)
    if (!task) return { ok: false, reasons: ["no task text captured for this session"] }
    return askAutoDecision(engine, python, workspace, task, path, sessionID)
  }

  try {
    if (ctx && ctx.session && typeof ctx.session.hook === "function") {
      await ctx.session.hook("prompt", (event) => {
        const text = event && event.prompt && typeof event.prompt.text === "string"
          ? event.prompt.text.trim() : ""
        if (text) sessionTasks.set(sessionKey(event), text)
      })
    }
  } catch {
    // Activation already injects context; a failed task cache only means the
    // guard cannot auto-create an ATOMIC decision and will deny instead.
  }

  const decide = async (path, session) => {
    const key = `${path}|${session || ""}`
    const cached = cache.get(key)
    if (cached && Date.now() - cached.at < CACHE_MS) return cached.value
    const value = await askEngine(engine, python, workspace, path, session)
    cache.set(key, { at: Date.now(), value })
    while (cache.size > 200) cache.delete(cache.keys().next().value)
    return value
  }

  const onBefore = async (input, output) => {
    const tool = String(input && input.tool || "")
    const sessionID = input && input.sessionID
    const args = output && output.args || {}
    const summary = summaryOf(tool, args)

    if (READ_TOOLS.has(tool)) {
      const path = readTarget(tool, args)
      if (path) {
        const skill = skillFromPath(path, workspace)
        if (skill) pendingReads.set(callIDOf(input), skill)
      }
    }

    if (WRITE_TOOLS.has(tool)) {
      const path = writeTarget(tool, args)
      if (!path) {
        // Measured on OpenCode 2.2.5: execute.before carries no args for write
        // either. The permission hook exposes {action:"write", resources:[path]}.
        // Fail closed only when that hook is missing.
        if (permissionHookAvailable) return
        record(workspace, sessionID, { ...summary, verdict: "DENY", reason: "write path not inspectable" })
        throw new Error("EOS BLOCKED: write tool path cannot be inspected and the permission hook is unavailable")
      }
      const gate = await askWriteGate(engine, python, workspace, path, sessionID)
      if (!gate.allow && !gate.needs_decision) {
        record(workspace, sessionID, { ...summary, verdict: "DENY", reason: gate.reasons || [] })
        await recordBlocked(engine, python, workspace, sessionID, path,
                            gate.reasons || [], "", gate.map_state, gate.map_sha256)
        throw new Error(`EOS BLOCKED: ${path} (map=${gate.map_state || "UNKNOWN"}). ` +
          `Reasons: ${(gate.reasons || []).join("; ").slice(0, MAX_REASON)}. ` +
          `Next: python "${join(engine, "tools", "recovery.py")}" diagnose --path "${path}"`)
      }
      if (gate.allow) {
        noteWrite(path, sessionID, callIDOf(input), {
          decision: gate.decision || "", risk: gate.risk || "",
          task: taskFor(sessionID),
        })
        record(workspace, sessionID, { ...summary, verdict: "ALLOW",
          decision: gate.decision || "", reason: `write gate: map=${gate.map_state}` })
        return
      }
      const decision = await decide(path, sessionID)
      if (!decision.ok) {
        const auto = await allowAtomic(sessionID, path)
        if (auto.ok) {
          noteWrite(path, sessionID, callIDOf(input), {
            decision: auto.decision, risk: auto.risk, task: taskFor(sessionID),
          })
          record(workspace, sessionID, { ...summary, verdict: "ALLOW",
            decision: auto.decision, reason: "auto ATOMIC decision" })
          return
        }
        record(workspace, sessionID, { ...summary, verdict: "DENY",
          reason: (auto.reasons || decision.reasons || []) })
        await recordBlocked(engine, python, workspace, sessionID, path,
                            auto.reasons || decision.reasons || [], "", gate.map_state,
                            gate.map_sha256)
        throw new Error(`EOS BLOCKED: no approved decision covers ${path}. ` +
          `EOS tried to prepare one automatically: ${(auto.reasons || []).join("; ").slice(0, MAX_REASON)}. ` +
          `Next: ${auto.next || "open a decision with auto_decision.py prepare"}`)
      }
      noteWrite(path, sessionID, callIDOf(input), {
        decision: decision.decision, risk: decision.risk, task: taskFor(sessionID),
      })
      record(workspace, sessionID, { ...summary, verdict: "ALLOW", decision: decision.decision })
      return
    }

    if (SHELL_TOOLS.has(tool)) {
      const command = shellCommand(args)
      if (!command) {
        // Measured on OpenCode 2.2.5: execute.before for shell carries no args.
        // The permission hook is the only place the command is visible. If that
        // hook is unavailable, fail closed instead of pretending to inspect it.
        if (permissionHookAvailable) return
        record(workspace, sessionID, { ...summary, verdict: "DENY", reason: "shell not inspectable" })
        throw new Error("EOS BLOCKED: shell command cannot be inspected and the permission hook is unavailable")
      }
      const verdict = classifyShell(command, workspace)
      if (verdict.effect === "deny") {
        record(workspace, sessionID, { ...summary, verdict: "DENY", reason: verdict.reason })
        throw new Error(`EOS BLOCKED: shell command refused (${verdict.reason}). ` +
          `Run it through an approved EOS tool, or ask the implementer to use an approved write path.`)
      }
      if (verdict.effect === "ask") {
        if (permissionHookAvailable) return
        record(workspace, sessionID, { ...summary, verdict: "DENY", reason: verdict.reason })
        throw new Error(`EOS ASK: shell command requires approval (${verdict.reason}), ` +
          `but the permission hook is unavailable.`)
      }
      record(workspace, sessionID, { ...summary, verdict: "ALLOW", reason: verdict.reason })
    }
  }

  const onPermission = async (input) => {
    if (!input || typeof input !== "object") return
    const verdict = permissionDecision(input, workspace)
    if (verdict.kind === "shell") {
      if (verdict.effect === "deny") {
        input.effect = "deny"
        input.message = verdict.reason
        record(workspace, input.sessionID, {
          kind: "permission", action: "shell", resource: verdict.resource,
          verdict: "DENY", reason: verdict.reason,
        })
        return
      }
      if (verdict.effect === "ask" && verdict.targets && verdict.targets.length) {
        // A shell write is allowed only when an approved EDR already covers
        // every target. No auto-created decision for shell writes.
        let allAllowed = true
        let decision = ""
        for (const target of verdict.targets) {
          const gate = await askWriteGate(engine, python, workspace, target,
                                          input.sessionID)
          if (!gate.allow) {
            allAllowed = false
            break
          }
          decision = gate.decision || decision
        }
        if (allAllowed) {
          input.effect = "allow"
          record(workspace, input.sessionID, {
            kind: "permission", action: "shell", resource: verdict.resource,
            verdict: "ALLOW", decision,
            reason: "approved EDR covers the shell write target",
          })
          return
        }
      }
      if (verdict.effect === "ask") {
        input.effect = "ask"
        input.message = verdict.reason
        record(workspace, input.sessionID, {
          kind: "permission", action: "shell", resource: verdict.resource,
          verdict: "ASK", reason: verdict.reason,
        })
        return
      }
      input.effect = "allow"
      record(workspace, input.sessionID, {
        kind: "permission", action: "shell", resource: verdict.resource,
        verdict: "ALLOW", reason: verdict.reason,
      })
      return
    }
    if (verdict.kind === "read") {
      const skill = skillFromPath(verdict.resource, workspace)
      if (skill) recordSkill(workspace, input.sessionID, skill)
    }
    if (verdict.allow === true) {
      record(workspace, input.sessionID, {
        kind: "permission", action: verdict.kind, resource: verdict.resource,
        verdict: "ALLOW", reason: verdict.reason,
      })
      return
    }
    if (verdict.kind === "write") {
      const path = verdict.resource
      if (!path) {
        input.effect = "deny"
        record(workspace, input.sessionID, {
          kind: "permission", action: "write", verdict: "DENY", reason: "write without a path",
        })
        return
      }
      const gate = await askWriteGate(engine, python, workspace, path, input.sessionID)
      if (!gate.allow && !gate.needs_decision) {
        input.effect = "deny"
        input.message = `EOS BLOCKED: ${path}. ` +
          `${(gate.reasons || []).join("; ").slice(0, MAX_REASON)}. ` +
          `Next: python "${join(engine, "tools", "recovery.py")}" diagnose --path "${path}"`
        record(workspace, input.sessionID, {
          kind: "permission", action: "write", resource: path,
          verdict: "DENY", reason: gate.reasons || [],
        })
        await recordBlocked(engine, python, workspace, input.sessionID, path,
                            gate.reasons || [], "", gate.map_state, gate.map_sha256)
        return
      }
      if (gate.allow) {
        noteWrite(path, input.sessionID, callIDOf(input), {
          decision: gate.decision || "", risk: gate.risk || "",
          task: taskFor(input.sessionID),
        })
        record(workspace, input.sessionID, {
          kind: "permission", action: "write", resource: path,
          verdict: "ALLOW", decision: gate.decision || "",
          reason: `write gate: map=${gate.map_state}`,
        })
        return
      }
      const decision = await decide(path, input.sessionID)
      if (!decision.ok) {
        const auto = await allowAtomic(input.sessionID, path)
        if (auto.ok) {
          noteWrite(path, input.sessionID, callIDOf(input), {
            decision: auto.decision, risk: auto.risk, task: taskFor(input.sessionID),
          })
          record(workspace, input.sessionID, {
            kind: "permission", action: "write", resource: path,
            verdict: "ALLOW", decision: auto.decision, reason: "auto ATOMIC decision",
          })
          return
        }
        input.effect = "deny"
        input.message = `EOS BLOCKED: ${path}. ` +
          `${(auto.reasons || decision.reasons || []).join("; ").slice(0, MAX_REASON)}. ` +
          `Next: python "${join(engine, "tools", "recovery.py")}" diagnose --path "${path}"`
        record(workspace, input.sessionID, {
          kind: "permission", action: "write", resource: path,
          verdict: "DENY", reason: auto.reasons || decision.reasons || [],
        })
        await recordBlocked(engine, python, workspace, input.sessionID, path,
                            auto.reasons || decision.reasons || [], "", gate.map_state,
                            gate.map_sha256)
        return
      }
      noteWrite(path, input.sessionID, callIDOf(input), {
        decision: decision.decision, risk: decision.risk,
        task: taskFor(input.sessionID),
      })
      record(workspace, input.sessionID, {
        kind: "permission", action: "write", resource: path,
        verdict: "ALLOW", decision: decision.decision,
      })
    }
  }

  try {
    if (ctx && ctx.tool && typeof ctx.tool.hook === "function") {
      await ctx.tool.hook("execute.before", onBefore)
      await ctx.tool.hook("execute.after", async (input, output) => {
        const tool = String(input && input.tool || "")
        const summary = summaryOf(tool, input && input.args)
        const pending = WRITE_TOOLS.has(tool)
          ? takeWrite(input && input.sessionID, summary, callIDOf(input))
          : null
        if (READ_TOOLS.has(tool)) {
          const key = callIDOf(input)
          const skill = pendingReads.get(key) || skillFromReadOutput(output, workspace)
          pendingReads.delete(key)
          if (skill && !toolFailed(output)) {
            recordSkill(workspace, input && input.sessionID, skill)
          }
        }
        let touched = null
        if (pending && toolFailed(output)) {
          record(workspace, input && input.sessionID, {
            kind: "tool-failed", path: pending.path,
            verdict: "NO_WORK_STATE", reason: "the write tool did not succeed",
          })
        } else if (pending) {
          touched = await touchWrite(engine, python, workspace, pending.path,
                                     input && input.sessionID)
          record(workspace, input && input.sessionID, {
            kind: "post-write", path: pending.path,
            verdict: touched.ok ? "TOUCHED" : "TOUCH_FAILED",
            map_state: touched.map_state || "",
            reason: touched.reason || "",
          })
          if (touched.ok) {
            if (pending.decision) {
              const change = await recordChange(engine, python, workspace,
                                                pending.decision, pending.path,
                                                input && input.sessionID)
              record(workspace, input && input.sessionID, {
                kind: "change-evidence", path: pending.path,
                decision: pending.decision,
                verdict: change.ok ? "RECORDED" : "FAILED",
                reason: change.ok ? "" : change.reason || "",
              })
            }
            const atomic = await recordAtomic(engine, python, workspace, pending,
                                              touched, input && input.sessionID)
            const status = String(atomic.status ||
              (atomic.ok ? "skipped" : "failed")).toUpperCase()
            record(workspace, input && input.sessionID, {
              kind: "work-state", path: pending.path,
              decision: pending.decision || "",
              verdict: status === "RECORDED" ? "CLOSED" : status,
              reason: atomic.reason || (atomic.record_id ? `record=${atomic.record_id}` : ""),
            })
          }
        }
        record(workspace, input && input.sessionID, {
          ...summary,
          verdict: "AFTER",
          post_write: touched ? (touched.ok ? "done" : "failed") : "not-a-write",
          output_sha256: sha256(output && output.output || "").slice(0, 32),
          output_chars: String(output && output.output || "").length,
        })
      })
      hooks.push("tool.execute.before", "tool.execute.after")
      enforcement = "enforced"
    } else {
      enforcement = "unavailable: ctx.tool.hook"
    }
    if (ctx && ctx.permission && typeof ctx.permission.hook === "function") {
      await ctx.permission.hook("evaluate", onPermission)
      hooks.push("permission.evaluate")
      permissionHookAvailable = true
    }
  } catch (error) {
    enforcement = `unavailable: ${error && error.message ? error.message : error}`
  }

  record(workspace, "guard", {
    kind: "capability", enforcement, hooks, engine,
    pid: process.pid,
  })
  if (process.env.EOS_GUARD_DEBUG) {
    console.error("EOS guard:", enforcement, hooks.join(","))
  }
}

export default {
  id: "eos.guard",
  async setup(ctx) {
    await install(ctx)
  },
}
