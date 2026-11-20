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
import { appendFileSync, mkdirSync, existsSync, readdirSync } from "node:fs"
import { createHash } from "node:crypto"
import { dirname, join, resolve } from "node:path"
import { tmpdir } from "node:os"

const TIMEOUT_MS = 8000
const CACHE_MS = 1500
const MAX_REASON = 400

const WRITE_TOOLS = new Set([
  "write", "edit", "patch", "multiedit", "apply_patch", "write_file",
  "edit_file", "create_file", "str_replace_editor",
])

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
  "python .agents/tools/work_state.py",
  "python .agents/tools/preflight.py",
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

function pythonArgs(engine, workspace) {
  return [join(engine, "tools", "decision.py"), "require-edit", "--json",
    "--workspace", workspace]
}

/**
 * Run `decision.py require-edit` and read the last JSON line.
 * A missing engine, a crash or a timeout is DENY, never ALLOW: the guard must
 * fail closed.
 */
function askEngine(engine, python, workspace, path, timeoutMs = TIMEOUT_MS) {
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
      child = spawn(python, [...pythonArgs(engine, workspace), "--path", path], {
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
function askAutoDecision(engine, python, workspace, task, path, timeoutMs = 12000) {
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
        "--scope", path, "--approve", "--json",
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

/**
 * Classify a shell command. Returns { allow, reason }.
 * Read-only is an allowlist. A write pattern is denied even when it also looks
 * read-only, because `pytest > out.txt` is still a write.
 */
export function classifyShell(command) {
  const text = String(command || "").replace(/\\/g, "/").replace(/\s+/g, " ").trim()
  if (!text) return { allow: false, reason: "empty shell command" }
  const unquoted = text.replace(/"[^"]*"|'[^']*'/g, "\"\"")
  if (/[;]|&&|\|\|/.test(unquoted) || /[`]|\$\(/.test(text)) {
    return { allow: false, reason: "command chaining is not allowed; one command per call" }
  }
  for (const pattern of WRITE_PATTERNS) {
    if (pattern.test(text)) return { allow: false, reason: "command can write or change state" }
  }
  const lower = text.toLowerCase()
  for (const rule of DENIED_ARGS) {
    if ((lower === rule.prefix || lower.startsWith(rule.prefix + " ")) && lower.includes(rule.token)) {
      return { allow: false, reason: `forbidden argument ${rule.token} for ${rule.prefix}` }
    }
  }
  if (lower.startsWith("eos install") && lower.includes(".agents")) {
    return { allow: true, reason: "trusted local installer into .agents" }
  }
  for (const prefix of READ_ONLY_PREFIXES) {
    if (lower === prefix.toLowerCase() || lower.startsWith(prefix.toLowerCase() + " ")) {
      return { allow: true, reason: `read-only prefix: ${prefix}` }
    }
  }
  return { allow: false, reason: "unclassified shell command (fail closed)" }
}

const WRITE_ACTIONS = new Set(["write", "edit", "patch", "multiedit", "apply_patch"])
const SHELL_ACTIONS = new Set(["shell", "bash", "sh", "terminal"])

/**
 * The V2 permission input is the only place the host exposes the shell command:
 *
 *   { sessionID, agent, action: "shell",
 *     resources: ["Set-Content -Path x -Value OK"], effect: "allow" }
 *
 * `tool.execute.before` for shell receives an empty args object (measured on
 * OpenCode 2.2.5), so the command can only be judged here. `input.effect` is
 * the supported deny switch: setting it to "deny" blocks the call.
 */
export function permissionDecision(input) {
  const action = String(input && input.action || "")
  const resources = Array.isArray(input && input.resources) ? input.resources.map(String) : []
  if (SHELL_ACTIONS.has(action)) {
    for (const command of resources) {
      const verdict = classifyShell(command)
      if (!verdict.allow) return { kind: "shell", allow: false, resource: command, reason: verdict.reason }
    }
    return { kind: "shell", allow: true, resource: resources.join(" && "), reason: "read-only" }
  }
  if (WRITE_ACTIONS.has(action)) {
    return { kind: "write", allow: null, resource: resources[0] || "", reason: "needs decision lookup" }
  }
  if (action === "read") return { kind: "read", allow: true, resource: resources.join(","), reason: "read" }
  return { kind: action || "unknown", allow: true, resource: resources.join(","), reason: "not a write surface" }
}

function evidencePath(workspace, sessionID) {
  const safe = String(sessionID || "default").replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 80)
  return join(workspace, ".agents", "evidence", "tools", `${safe}.jsonl`)
}

function record(workspace, sessionID, entry) {
  try {
    const path = evidencePath(workspace, sessionID)
    mkdirSync(dirname(path), { recursive: true })
    appendFileSync(path, JSON.stringify({ ts: now(), ...entry }) + "\n", "utf8")
  } catch {
    // Evidence loss must not crash the host, but it must not turn a deny into
    // an allow either: recording happens after the verdict is computed.
  }
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

async function install(ctx, options) {
  const workspace = workspaceOf(ctx, options && options.workspace)
  const { engine, python } = engineFor(workspace)
  const cache = new Map()
  const sessionTasks = new Map()
  let enforcement = "unknown"
  let hooks = []
  let permissionHookAvailable = false

  const taskFor = (sessionID) => sessionTasks.get(sessionKey({ sessionID })) || ""
  const allowAtomic = async (sessionID, path) => {
    const task = taskFor(sessionID)
    if (!task) return { ok: false, reasons: ["no task text captured for this session"] }
    return askAutoDecision(engine, python, workspace, task, path)
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

  const decide = async (path) => {
    const key = `${path}`
    const cached = cache.get(key)
    if (cached && Date.now() - cached.at < CACHE_MS) return cached.value
    const value = await askEngine(engine, python, workspace, path)
    cache.set(key, { at: Date.now(), value })
    while (cache.size > 200) cache.delete(cache.keys().next().value)
    return value
  }

  const onBefore = async (input, output) => {
    const tool = String(input && input.tool || "")
    const sessionID = input && input.sessionID
    const args = output && output.args || {}
    const summary = summaryOf(tool, args)

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
      const decision = await decide(path)
      if (!decision.ok) {
        const auto = await allowAtomic(sessionID, path)
        if (auto.ok) {
          record(workspace, sessionID, { ...summary, verdict: "ALLOW",
            decision: auto.decision, reason: "auto ATOMIC decision" })
          return
        }
        record(workspace, sessionID, { ...summary, verdict: "DENY",
          reason: (auto.reasons || decision.reasons || []) })
        throw new Error(`EOS BLOCKED: no approved decision covers ${path}. ` +
          `EOS tried to prepare one automatically: ${(auto.reasons || []).join("; ").slice(0, MAX_REASON)}. ` +
          `Next: ${auto.next || "open a decision with auto_decision.py prepare"}`)
      }
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
      const verdict = classifyShell(command)
      if (!verdict.allow) {
        record(workspace, sessionID, { ...summary, verdict: "DENY", reason: verdict.reason })
        throw new Error(`EOS BLOCKED: shell command refused (${verdict.reason}). ` +
          `Run it through an approved EOS tool, or ask the implementer to use an approved write path.`)
      }
      record(workspace, sessionID, { ...summary, verdict: "ALLOW", reason: verdict.reason })
    }
  }

  const onPermission = async (input) => {
    if (!input || typeof input !== "object") return
    const verdict = permissionDecision(input)
    if (verdict.allow === true) {
      record(workspace, input.sessionID, {
        kind: "permission", action: verdict.kind, resource: verdict.resource,
        verdict: "ALLOW", reason: verdict.reason,
      })
      return
    }
    if (verdict.kind === "shell") {
      input.effect = "deny"
      record(workspace, input.sessionID, {
        kind: "permission", action: "shell", resource: verdict.resource,
        verdict: "DENY", reason: verdict.reason,
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
      const decision = await decide(path)
      if (!decision.ok) {
        const auto = await allowAtomic(input.sessionID, path)
        if (auto.ok) {
          record(workspace, input.sessionID, {
            kind: "permission", action: "write", resource: path,
            verdict: "ALLOW", decision: auto.decision, reason: "auto ATOMIC decision",
          })
          return
        }
        input.effect = "deny"
        record(workspace, input.sessionID, {
          kind: "permission", action: "write", resource: path,
          verdict: "DENY", reason: auto.reasons || decision.reasons || [],
        })
        return
      }
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
        record(workspace, input && input.sessionID, {
          ...summaryOf(String(input && input.tool || ""), input && input.args),
          verdict: "AFTER",
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
