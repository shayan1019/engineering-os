// EOS automatic activation for OpenCode.
//
// Why this file exists
// --------------------
// The user should not have to pick "Chief Architect" for every engineering
// task. This plugin hooks the host's own `chat.params` lifecycle event and
// injects a bounded EOS context block: the project, the rules in effect, the
// skills that are relevant, the smallest competent specialist set and the gate
// that will judge the result.
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

import { spawn } from "node:child_process"
import { mkdtempSync, writeFileSync, rmSync, readdirSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, dirname, resolve } from "node:path"

const MAX_BLOCK_CHARS = 2400
const TIMEOUT_MS = 20000

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
  if (!python) {
    let newest = ""
    try {
      for (const name of readdirSync(versions)) {
        if (name > newest) newest = name
      }
    } catch {
      // No versions directory: fall back to the interpreter on PATH.
    }
    if (newest) {
      python = join(versions, newest, "runtime", "python", process.platform === "win32" ? "python.exe" : "bin/python3")
    }
  }
  if (!python) python = process.platform === "win32" ? "python" : "python3"
  let engine = process.env.EOS_ENGINE || ""
  if (!engine && python.includes("versions")) {
    engine = resolve(dirname(python), "..", "..", "engine")
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

function lastUserText(input) {
  const messages = input?.message?.parts || input?.parts || []
  const texts = []
  for (const part of messages) {
    if (typeof part?.text === "string" && part.text.trim()) texts.push(part.text.trim())
  }
  if (!texts.length && typeof input?.message?.text === "string") texts.push(input.message.text)
  return texts.length ? texts[texts.length - 1] : ""
}

export default (async ({ directory, worktree }) => {
  const cwd = worktree || directory || process.cwd()
  return {
    "chat.params": async (input, output) => {
      let taskFile = null
      try {
        const task = lastUserText(input)
        // An empty turn carries no task: nothing to resolve.
        if (!task || task.length > 4000) return
        // The task is handed over in a file, never on a command line: a task
        // may contain quotes, ampersands and pipes, and none of them may be
        // interpreted by anything.
        const dir = mkdtempSync(join(tmpdir(), "eos-task-"))
        taskFile = join(dir, "task.txt")
        writeFileSync(taskFile, task, "utf8")
        const command = activateCommand()
        const block = await run([...command, "--task-file", taskFile], cwd)
        if (!block) return
        const text = block.length > MAX_BLOCK_CHARS ? block.slice(0, MAX_BLOCK_CHARS) : block
        if (Array.isArray(output?.system)) output.system.push(text)
        else if (typeof output?.system === "string") output.system = output.system + "\n" + text
      } catch (e) {
        // Activation is an enhancement. It must never break the host session.
        // The failure is silent by default and loud when it is being debugged.
        if (process.env.EOS_ACTIVATION_DEBUG) {
          console.error("EOS activation error:", e && e.stack ? e.stack : e)
        }
      } finally {
        if (taskFile) {
          try {
            rmSync(dirname(taskFile), { recursive: true, force: true })
          } catch {}
        }
      }
    },
  }
})
