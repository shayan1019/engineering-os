// EOS automatic activation for OpenCode.
//
// Why this file exists
// --------------------
// The user should not have to pick "Chief Architect" for every engineering
// task. This plugin hooks the host's own session lifecycle and injects a
// bounded EOS context block: the project, the rules in effect, the skills that
// are relevant, the smallest competent specialist set and the gate that will
// judge the result.
//
// What it deliberately does NOT do
// --------------------------------
// * It never adds a tool, never grants a permission and never changes the
//   host's mode. Plan stays read-only and Build stays authorised-bounded
//   because the *host* decides that, not EOS.
// * It never overwrites an existing plugin, an MCP configuration or a hook.
// * It never loads a skill body. The context block names skills; the model
//   reads a skill file only when the task actually needs it.
// * If the EOS engine is missing, the plugin does nothing. A missing engine
//   must not break the host.
//
// The block is produced by `eos activate --task "<text>"`, which is the same
// router the rest of EOS uses, so the activation cannot disagree with the
// gate or the router.
//
// Host API
// --------
// OpenCode 2 default-exports a plugin definition with an `id` and `setup(ctx)`.
// The task is captured once per prompt (`prompt` hook) and injected right
// before the agent's model request (`context` hook), so one user turn activates
// EOS once instead of once per model step. A V1 host calls `server()`; both
// shapes share the same core, and neither imports anything the host has to
// provide.

import { spawn } from "node:child_process"
import { mkdtempSync, writeFileSync, rmSync, readdirSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, dirname, resolve } from "node:path"

const MAX_BLOCK_CHARS = 2400
const MAX_TASK_CHARS = 4000
const TIMEOUT_MS = 20000
const CACHE_LIMIT = 32

/**
 * The command that produces the context block.
 *
 * The engine is invoked directly (python + the engine script) instead of
 * through a shell: a task can contain quotes, ampersands and pipes, and none
 * of them may reach a command interpreter. `EOS_ACTIVATE_COMMAND` (a JSON
 * array) exists so the test can substitute a fake engine.
 */
function activateCommand() {
  const override = process.env.EOS_ACTIVATE_COMMAND
  if (override) {
    try {
      const parsed = JSON.parse(override)
      if (Array.isArray(parsed) && parsed.length) return parsed.map(String)
    } catch {
      // A broken override is ignored; the normal resolution continues.
    }
  }
  const home = process.env.EOS_HOME || (process.env.USERPROFILE || process.env.HOME || "") + "/.eos"
  const versions = join(home, "versions")
  let python = process.env.EOS_PYTHON || ""
  let activeVersion = ""
  if (!python) {
    // Read the active version from config.json instead of picking the newest
    // by name. After a rollback the active version is the older one, and
    // picking by name would silently use the newer (rolled-back-from) engine.
    try {
      const configRaw = readFileSync(join(home, "config.json"), "utf8")
      const config = JSON.parse(configRaw)
      activeVersion = config.active_version || config.current || ""
      if (!activeVersion) {
        // Config exists but no active version: degraded
        console.error("EOS: config.json has no active_version; activation degraded")
        activeVersion = ""
      }
    } catch {
      // No config or unreadable: do NOT fall back to newest by name.
      // This could mask a rollback failure. Report degraded and skip.
      console.error("EOS: cannot read config.json; activation degraded (no silent fallback)")
      activeVersion = ""
    }
    if (activeVersion) {
      python = join(versions, activeVersion, "runtime", "python", process.platform === "win32" ? "python.exe" : "bin/python3")
    }
  }
  if (!python) python = process.platform === "win32" ? "python" : "python3"
  let engine = process.env.EOS_ENGINE || ""
  if (!engine && activeVersion) {
    engine = resolve(versions, activeVersion, "engine")
  }
  if (!engine) engine = join(home, "engine")
  return [python, join(engine, "tools", "eos_activate.py")]
}

function run(args, cwd) {
  return new Promise((resolvePromise) => {
    let settled = false
    const done = (value) => {
      if (settled) return
      settled = true
      resolvePromise(value)
    }
    let child
    try {
      child = spawn(args[0], args.slice(1), { cwd, windowsHide: true })
    } catch {
      done(null)
      return
    }
    let out = ""
    let err = ""
    child.stdout?.on("data", (d) => {
      if (out.length < MAX_BLOCK_CHARS * 2) out += d.toString()
    })
    child.stderr?.on("data", (d) => {
      if (err.length < 2000) err += d.toString()
    })
    child.on("error", () => done(null))
    const timer = setTimeout(() => {
      try {
        child.kill()
      } catch {}
      done(null)
    }, TIMEOUT_MS)
    child.on("close", (code) => {
      clearTimeout(timer)
      if (code === 0 && out.trim()) done(out.trim())
      else done(null)
    })
  })
}

/**
 * Activation is an enhancement. It must never break the host session: the
 * failure is silent by default and loud when it is being debugged.
 */
function debug(error) {
  if (!process.env.EOS_ACTIVATION_DEBUG) return
  console.error("EOS activation error:", error && error.stack ? error.stack : error)
}

async function activateBlock(task, cwd) {
  let taskFile = null
  try {
    // The task is handed over in a file, never on a command line: a task may
    // contain quotes, ampersands and pipes, and none of them may be
    // interpreted by anything.
    const dir = mkdtempSync(join(tmpdir(), "eos-task-"))
    taskFile = join(dir, "task.txt")
    writeFileSync(taskFile, task, "utf8")
    const block = await run([...activateCommand(), "--task-file", taskFile], cwd)
    if (!block) return ""
    return block.length > MAX_BLOCK_CHARS ? block.slice(0, MAX_BLOCK_CHARS) : block
  } catch (error) {
    debug(error)
    return ""
  } finally {
    if (taskFile) {
      try {
        rmSync(dirname(taskFile), { recursive: true, force: true })
      } catch {}
    }
  }
}

/**
 * One activation per session and task.
 *
 * The host asks for the context once per model step, and a task that calls
 * tools has many steps. Running the router again for every step would cost a
 * subprocess per step and let the block drift mid-turn, so the result is
 * cached by the task text and reused until the user says something else.
 * The `prompt` hook warms the entry; `context` consumes it.
 */
const activations = new Map()

function activationFor(key, task, cwd) {
  const known = activations.get(key)
  if (known && known.task === task) return known.block
  const block = activateBlock(task, cwd)
  activations.delete(key)
  activations.set(key, { task, block })
  while (activations.size > CACHE_LIMIT) {
    const oldest = activations.keys().next().value
    if (oldest === undefined) break
    activations.delete(oldest)
  }
  return block
}

/**
 * A task text, or "" when the turn carries nothing the router can resolve.
 * An empty turn carries no task, and an oversized message is not a task.
 */
function taskText(value) {
  if (typeof value !== "string") return ""
  const text = value.trim()
  if (!text || text.length > MAX_TASK_CHARS) return ""
  return text
}

function partText(part) {
  if (!part || typeof part.text !== "string") return ""
  if (typeof part.type === "string" && part.type !== "text") return ""
  return part.text.trim()
}

/**
 * The text of one message, read defensively: a host may hand over parts,
 * content or plain text, and this must survive a shape it does not expect.
 */
function messageText(message) {
  if (!message) return ""
  if (typeof message.text === "string") return message.text.trim()
  const content = message.parts ?? message.content
  if (typeof content === "string") return content.trim()
  if (!Array.isArray(content)) return ""
  const texts = []
  for (const part of content) {
    const text = partText(part)
    if (text) texts.push(text)
  }
  return texts.join("\n").trim()
}

function latestUserTask(messages) {
  if (!Array.isArray(messages)) return ""
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message && typeof message.role === "string" && message.role !== "user") continue
    const text = taskText(messageText(message))
    if (text) return text
  }
  return ""
}

/** V1 delivered the turn as `input.message.parts`. */
function lastUserText(input) {
  const messages = input?.message?.parts || input?.parts || []
  const texts = []
  for (const part of messages) {
    if (typeof part?.text === "string" && part.text.trim()) texts.push(part.text.trim())
  }
  if (!texts.length && typeof input?.message?.text === "string") texts.push(input.message.text)
  return texts.length ? texts[texts.length - 1] : ""
}

function sessionKey(event) {
  const id = event && typeof event.sessionID === "string" ? event.sessionID : ""
  return id || "default"
}

async function resolveBlock(event, cwd) {
  const key = sessionKey(event)
  // The context event carries the transcript, not the prompt draft, so the
  // task comes from the messages when they are readable and from the entry the
  // prompt hook warmed otherwise.
  const task = taskText(event && event.prompt && event.prompt.text) || latestUserTask(event && event.messages)
  if (task) return await activationFor(key, task, cwd)
  const known = activations.get(key)
  return known ? await known.block : ""
}

async function install(ctx) {
  const cwd = (ctx && ctx.location && ctx.location.directory) || process.cwd()

  // Prompt admission is the one moment the task text is known exactly. Warming
  // the activation here keeps the model request itself from waiting on the
  // router, and it runs once per prompt instead of once per model step.
  await ctx.session.hook("prompt", (event) => {
    try {
      const key = sessionKey(event)
      const task = taskText(event && event.prompt && event.prompt.text)
      // A turn without a resolvable task must not inherit the previous turn's
      // activation, so the entry is dropped instead of left behind.
      if (!task) {
        activations.delete(key)
        return
      }
      const known = activations.get(key)
      if (!known || known.task !== task) activationFor(key, task, cwd)
    } catch (error) {
      debug(error)
    }
  })

  await ctx.session.hook("context", async (event) => {
    try {
      const block = await resolveBlock(event, cwd)
      if (!block) return
      if (Array.isArray(event.system)) event.system.push({ type: "text", text: block })
    } catch (error) {
      debug(error)
    }
  })
}

async function serveV1(input, output, cwd) {
  try {
    const task = taskText(lastUserText(input))
    if (!task) return
    const block = await activationFor(sessionKey(input), task, cwd)
    if (!block) return
    if (Array.isArray(output?.system)) output.system.push(block)
    else if (typeof output?.system === "string") output.system = output.system + "\n" + block
  } catch (error) {
    debug(error)
  }
}

export default {
  // V2 reads this definition: `id` identifies the plugin in status and
  // diagnostics, `setup` registers hooks on the domains that own them.
  id: "eos.activation",
  async setup(ctx) {
    await install(ctx)
  },
  // V1 hosts call `server()`; V2 reads `id`/`setup` and ignores it. One
  // deployed file therefore works on either host.
  async server(options) {
    const cwd = (options && (options.worktree || options.directory)) || process.cwd()
    return {
      "chat.params": (input, output) => serveV1(input, output, cwd),
    }
  },
}
