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

import { spawn, spawnSync } from "node:child_process"
import { mkdtempSync, writeFileSync, rmSync, readFileSync, realpathSync, statSync, existsSync } from "node:fs"
import { appendFile, mkdir, readFile, realpath, rename, stat } from "node:fs/promises"
import { createHash, randomUUID } from "node:crypto"
import { homedir, tmpdir } from "node:os"
import { join, dirname, resolve, isAbsolute } from "node:path"

const MAX_BLOCK_CHARS = 8000
const MAX_TASK_CHARS = 4000
const TIMEOUT_MS = 60000
const CACHE_LIMIT = 32

// ENGINE-RESOLVER BEGIN
// This block is identical in eos-guard.js and eos-activation.js (a test enforces
// it). It is duplicated because the host loads every file in the plugin
// directory as a plugin, so a shared helper module cannot live there.
//
// Order, and nothing else:
//   1. <workspace>/.agents     the project's own control plane, explicit
//   2. EOS_ENGINE              explicit operator override
//   3. EOS_HOME/config.json    active_version, verified against its manifest
// Anything invalid returns `problem` and an empty engine. There is no fallback
// to another installed version: running an engine nobody activated is worse
// than refusing to run.
const VERSION_NAME = /^[0-9A-Za-z][0-9A-Za-z._+-]*$/
const samePath = (a, b) => {
  const left = realpathSync.native(a)
  const right = realpathSync.native(b)
  return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right
}
const readJson = (path) => JSON.parse(readFileSync(path, "utf8").replace(/^\uFEFF/, ""))
const digest = (data) => createHash("sha256").update(data).digest("hex").toUpperCase()

function usablePython(candidate) {
  if (!candidate) return false
  try {
    const probe = spawnSync(candidate, ["-c", "import sys; print(sys.version_info[0])"], {
      windowsHide: true, timeout: 1500, encoding: "utf8",
    })
    return probe.status === 0 && /^3\s*$/.test(String(probe.stdout || "").trim())
  } catch { return false }
}

function systemPython() {
  const configured = process.env.EOS_PYTHON || ""
  if (configured && isAbsolute(configured)) return configured
  const candidates = configured ? [configured] : []
  if (process.platform === "win32") {
    try {
      const located = spawnSync("where.exe", ["python"], { windowsHide: true, encoding: "utf8" })
      candidates.push(...String(located.stdout || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean))
    } catch {}
  }
  candidates.push(process.platform === "win32" ? "python" : "python3")
  return candidates.find(usablePython) || candidates[0]
}

function runtimePython(prefix) {
  return join(prefix, "runtime", "python", process.platform === "win32" ? "python.exe" : "bin/python3")
}

function activeEngine(home) {
  if (existsSync(join(home, "state", "activation-txn.json"))) {
    throw new Error("an EOS version change was interrupted; run: eos recover")
  }
  let config
  try {
    config = readJson(join(home, "config.json"))
  } catch (error) {
    throw new Error(`config.json is missing or invalid: ${error.message}`)
  }
  const active = config && config.active_version
  if (typeof active !== "string" || !VERSION_NAME.test(active)) {
    throw new Error("config.json has no valid active_version")
  }
  const records = (Array.isArray(config.versions) ? config.versions : [])
    .filter((item) => item && item.version === active)
  if (records.length !== 1) {
    throw new Error(`config.json has ${records.length} record(s) for active_version ${active}`)
  }
  const dir = join(home, "versions", active)
  if (!samePath(String(records[0].dir), dir)) {
    throw new Error(`active_version ${active} is not recorded at ${dir}`)
  }
  const manifest = readJson(join(dir, "install-manifest.json"))
  if (manifest.version !== active || !Array.isArray(manifest.entries)) {
    throw new Error(`install manifest does not describe ${active}`)
  }
  const entries = manifest.entries.filter((item) => String(item.rel).startsWith("engine/"))
  const feed = entries.map((item) => `${item.rel.slice(7)}:${item.sha256}\n`).join("")
  if (digest(feed) !== String(records[0].engine_manifest_sha256).toUpperCase()) {
    throw new Error(`install manifest hash does not match config.json for ${active}`)
  }
  for (const item of entries) {
    if (!/^engine\/tools\/[^/]+\.py$/.test(item.rel)) continue
    if (digest(readFileSync(join(dir, item.rel))) !== item.sha256) {
      throw new Error(`installed engine file was changed: ${item.rel}`)
    }
  }
  let python = process.env.EOS_PYTHON || ""
  if (!python && manifest.runtime_mode === "bundled") {
    python = runtimePython(dir)
    if (!existsSync(python)) throw new Error(`bundled runtime is missing for ${active}`)
  }
  return { engine: join(dir, "engine"), python: python || systemPython(), source: "active_version", version: active }
}

function locateEngine(workspace) {
  try {
    const local = workspace ? join(workspace, ".agents") : ""
    if (local && ["decision.py", "eos_activate.py"].some((name) => existsSync(join(local, "tools", name)))) {
      const bundled = [
        join(workspace, "runtime", "python", process.platform === "win32" ? "python.exe" : "bin/python3"),
        join(workspace, "engineering-os", "runtime", "python", process.platform === "win32" ? "python.exe" : "bin/python3"),
      ].find((candidate) => existsSync(candidate))
      return { engine: local, python: bundled || systemPython(), source: bundled ? "workspace-bundled" : "workspace", version: "" }
    }
    const override = process.env.EOS_ENGINE
    if (override) {
      if (!["decision.py", "eos_activate.py"].every((name) => existsSync(join(override, "tools", name)))) {
        throw new Error(`EOS_ENGINE is not an EOS engine: ${override}`)
      }
      const bundled = runtimePython(dirname(resolve(override)))
      const python = process.env.EOS_PYTHON || (existsSync(bundled) ? bundled : systemPython())
      return { engine: override, python, source: "EOS_ENGINE", version: "" }
    }
    return activeEngine(process.env.EOS_HOME ||
      join(process.env.USERPROFILE || process.env.HOME || "", ".eos"))
  } catch (error) {
    return { engine: "", python: "", source: "", version: "", problem: error.message }
  }
}
// ENGINE-RESOLVER END

/**
 * The command that produces the context block.
 *
 * The engine is invoked directly (python + the engine script) instead of
 * through a shell: a task can contain quotes, ampersands and pipes, and none
 * of them may reach a command interpreter. `EOS_ACTIVATE_COMMAND` (a JSON
 * array) exists so the test can substitute a fake engine.
 */
function activateCommand(cwd) {
  const override = process.env.EOS_ACTIVATE_COMMAND
  if (override) {
    try {
      const parsed = JSON.parse(override)
      if (Array.isArray(parsed) && parsed.length) return parsed.map(String)
    } catch {
      // A broken override is ignored; the normal resolution continues.
    }
  }
  const live = locateEngine(cwd)
  if (live.problem) throw new Error(live.problem)
  return [live.python, join(live.engine, "tools", "eos_activate.py")]
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
    } catch (error) {
      debug(error)
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
    child.on("error", (error) => {
      debug(error)
      done(null)
    })
    const timer = setTimeout(() => {
      try {
        child.kill()
      } catch {}
      done(null)
    }, TIMEOUT_MS)
    child.on("close", (code) => {
      clearTimeout(timer)
      if (code === 0 && out.trim()) done(out.trim())
      else {
        debug(new Error(`EOS activation subprocess exited ${code}; stdout=${out.length} bytes, stderr=${err.length} bytes`))
        done(null)
      }
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

function debugStatus(message) {
  if (process.env.EOS_ACTIVATION_DEBUG) console.error("EOS activation:", message)
}

async function activateBlock(task, cwd, session) {
  let taskFile = null
  try {
    // The task is handed over in a file, never on a command line: a task may
    // contain quotes, ampersands and pipes, and none of them may be
    // interpreted by anything.
    const dir = mkdtempSync(join(tmpdir(), "eos-task-"))
    taskFile = join(dir, "task.txt")
    writeFileSync(taskFile, task, "utf8")
    const args = [...activateCommand(cwd), "--task-file", taskFile]
    const metadataSupported = args[1] && await stat(join(dirname(args[1]), "eos_discipline.py"))
      .then((file) => file.isFile(), () => false)
    debugStatus(`request received; observation metadata ${metadataSupported ? "available" : "unavailable"}`)
    if (metadataSupported) args.push("--nexus-metadata")
    if (session) args.push("--session", String(session))
    let block = await run(args, cwd)
    if (!block) return unavailableBlock("the engine returned no context")
    if (metadataSupported && block.startsWith("EOS_NEXUS_V1 ")) {
      const newline = block.indexOf("\n")
      if (newline > 0) {
        const metadata = JSON.parse(block.slice(13, newline))
        block = block.slice(newline + 1)
        nexusActivated(cwd, session, metadata)
      }
    }
    debugStatus("context rendered")
    return block.length > MAX_BLOCK_CHARS ? block.slice(0, MAX_BLOCK_CHARS) : block
  } catch (error) {
    debug(error)
    return unavailableBlock(error && error.message ? error.message : "unknown error")
  } finally {
    if (taskFile) {
      try {
        rmSync(dirname(taskFile), { recursive: true, force: true })
      } catch {}
    }
  }
}

function unavailableBlock(reason) {
  const text = String(reason || "unknown").slice(0, 300)
  return "<eos-context status=\"UNAVAILABLE\">EOS activation failed: " + text +
    ". Do not assume EOS is active. The write guard still enforces decisions.</eos-context>"
}

function contextEpoch(cwd) {
  try {
    const path = join(cwd, ".agents", "state", "context.epoch")
    if (!existsSync(path)) return "0"
    return String(statSync(path).mtimeMs) + ":" + readFileSync(path, "utf8").trim()
  } catch {
    return "0"
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

function activationFor(key, task, cwd, session) {
  const live = locateEngine(cwd)
  const epoch = `${contextEpoch(cwd)}|${live.engine}|${live.problem || ""}`
  const known = activations.get(key)
  if (known && known.task === task && known.epoch === epoch) return known.block
  const block = activateBlock(task, cwd, session || key)
  activations.delete(key)
  activations.set(key, { task, epoch, block })
  while (activations.size > CACHE_LIMIT) {
    const oldest = activations.keys().next().value
    if (oldest === undefined) break
    activations.delete(oldest)
  }
  return block
}

/**
 * A task text, or "" when the turn carries nothing the router can resolve.
 */
function taskText(value) {
  if (typeof value !== "string") return ""
  // A long request is still a request: dropping it left exactly the biggest tasks
  // without EOS context. The router needs its beginning, so it is cut, not skipped.
  return value.trim().slice(0, MAX_TASK_CHARS)
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

const NEXUS_STREAM_BYTES = 2 * 1024 * 1024
const nexusSinks = new Map()

function safeIdentifier(value) {
  return typeof value === "string" && /^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,95}$/.test(value) &&
    !/(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]+|xox[baprs]-[A-Za-z0-9-]+|AKIA[A-Z0-9]{16})/i.test(value)
    ? value : undefined
}

function nexusHome() {
  return resolve(process.env.EOS_HOME || join(homedir(), ".eos"))
}

function nexusEvent(cwd, session, kind, fields = {}) {
  if (!/^[a-zA-Z0-9_-]{1,95}$/.test(session || "") || session === "default") return
  let sink = nexusSinks.get(cwd)
  if (!sink) {
    if (nexusSinks.size >= CACHE_LIMIT) return
    sink = { queue: [], losses: new Set(), sessions: new Set(), draining: false }
    nexusSinks.set(cwd, sink)
  }
  // A hot session never waits for disk. The observation queue drops excess
  // work and reports loss once the consumer can keep up again.
  if (sink.queue.length >= 128) {
    if (sink.losses.size < 64) sink.losses.add(session)
    return
  }
  sink.queue.push({ session, kind, fields, ts: new Date().toISOString() })
  if (sink.draining) return
  sink.draining = true
  setImmediate(() => void drainNexus(cwd, sink))
}

function encodeNexus(kind, fields, ts = new Date().toISOString()) {
  return JSON.stringify({ schema: 1, id: randomUUID(), ts, host: "opencode", kind, ...fields }) + "\n"
}

async function drainNexus(cwd, sink) {
  try {
    const normalized = (await realpath(cwd)).replace(/\\/g, "/").replace(/\/$/, "")
    const key = createHash("sha256").update(process.platform === "win32" ? normalized.toLowerCase() : normalized).digest("hex").slice(0, 24)
    const directory = join(await realpath(nexusHome()).catch(() => nexusHome()), "evidence", "nexus", key)
    await mkdir(directory, { recursive: true })
    const equal = (left, right) => process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right
    if (!equal(await realpath(directory), directory)) throw new Error("NEXUS evidence path is redirected")
    while (sink.queue.length || sink.losses.size) {
      const lost = sink.losses.values().next().value
      const event = sink.queue.length ? sink.queue.shift() : { session: lost, kind: "telemetry.loss", fields: {} }
      if (event.kind === "telemetry.loss") sink.losses.delete(event.session)
      const path = join(directory, `${event.session}.jsonl`)
      try {
        const existing = await realpath(path).catch(() => null)
        if (existing && !equal(existing, path)) continue
        const info = existing ? await stat(path) : null
        if (info && (!info.isFile() || info.nlink !== 1)) continue
        if (info && info.size >= NEXUS_STREAM_BYTES) {
          await rename(path, path + ".previous")
          await appendFile(path, encodeNexus("telemetry.loss", {}))
        }
        await appendFile(path, encodeNexus(event.kind, event.fields, event.ts))
      } catch {
        if (event.kind !== "telemetry.loss" && sink.losses.size < 64) sink.losses.add(event.session)
      }
    }
  } catch (error) {
    debug(error)
    for (const event of sink.queue.splice(0)) {
      if (sink.losses.size < 64) sink.losses.add(event.session)
    }
  } finally {
    sink.draining = false
  }
}

function nexusActivated(cwd, session, metadata) {
  if (!/^[a-zA-Z0-9_-]{1,95}$/.test(session || "") || session === "default") return
  const first = !nexusSinks.get(cwd)?.sessions.has(session)
  nexusEvent(cwd, session, first ? "session.started" : "session.resumed")
  const activeSink = nexusSinks.get(cwd)
  if (activeSink && !activeSink.sessions.has(session)) {
    activeSink.sessions.add(session)
    if (activeSink.sessions.size > 128) activeSink.sessions.delete(activeSink.sessions.values().next().value)
    if (process.platform === "win32") setImmediate(() => void tryNexusOpen(cwd, session))
  }
  if (metadata.routed === true) nexusEvent(cwd, session, "query.routed")
  if (metadata.resolved === true) nexusEvent(cwd, session, "context.resolved")
  for (const skill of (Array.isArray(metadata.skills) ? metadata.skills : []).slice(0, 8)) {
    if (safeIdentifier(skill)) nexusEvent(cwd, session, "skill.selected", { skill })
  }
}

async function tryNexusOpen(cwd, session) {
  try {
    const home = nexusHome()
    const prefPath = join(home, "nexus", "preferences.json")
    if ((await stat(prefPath)).size > 32768) return
    const preferences = JSON.parse((await readFile(prefPath, "utf8")).replace(/^\uFEFF/, ""))
    if (preferences.autoOpen !== true) return
    const configPath = join(home, "config.json")
    if ((await stat(configPath)).size > 32768) return
    const config = JSON.parse((await readFile(configPath, "utf8")).replace(/^\uFEFF/, ""))
    const version = config.active_version
    if (typeof version !== "string" || !VERSION_NAME.test(version)) return
    const prefix = join(home, "versions", version)
    const executable = join(prefix, "eos.exe")
    const manifest = JSON.parse(await readFile(join(prefix, "install-manifest.json"), "utf8"))
    const entry = manifest.entries?.find((item) => item.rel === "eos.exe")
    if (!entry || digest(await readFile(executable)) !== String(entry.sha256).toUpperCase()) return
    const child = spawn(executable, ["nexus", "--auto", "--workspace", cwd, "--session", session, "--host", "opencode"],
      { detached: true, stdio: "ignore", windowsHide: true })
    child.on("error", debug)
    child.unref()
  } catch { /* observing has no effect on the host */ }
}

function toolObservation(cwd, event, mode, children, pendingSkills) {
  const session = event?.sessionID
  const tool = safeIdentifier(event?.tool)
  if (!tool || !session || (!nexusSinks.get(cwd)?.sessions.has(session) && !children.has(session))) return
  const callId = event.callID || event.toolCallID || event.callId || event.id
  const correlation = typeof callId === "string" ? createHash("sha256").update(callId).digest("hex").slice(0, 24) : undefined
  nexusEvent(cwd, session, `tool.${mode}`, { tool, correlation })
  if (tool !== "skill") return
  const skill = safeIdentifier(event.input?.id || event.input?.name)
  const key = correlation ? `${session}:${correlation}` : null
  if (mode === "started" && skill && key) {
    if (pendingSkills.size >= 128) pendingSkills.delete(pendingSkills.keys().next().value)
    pendingSkills.set(key, skill)
  } else if (mode !== "started") {
    const loaded = skill || pendingSkills.get(key)
    pendingSkills.delete(key)
    if (mode === "completed" && loaded) nexusEvent(cwd, session, "skill.loaded", { skill: loaded, correlation })
    if (mode === "failed" && loaded) nexusEvent(cwd, session, "skill.unavailable", { skill: loaded, correlation })
  }
}

function observeRuntime(cwd, event, children, steps, activeAgents) {
  const data = event?.data || event?.properties || {}
  const location = event?.location?.directory || data.location?.directory
  const normalized = (path) => process.platform === "win32" ? resolve(path).toLowerCase() : resolve(path)
  if (typeof location === "string" && normalized(location) !== normalized(cwd)) return
  const session = data.sessionID
  if (!session) return
  const known = (id) => nexusSinks.get(cwd)?.sessions.has(id) || children.has(id)
  if (event.type === "session.created" && data.parentID && known(data.parentID)) {
    if (children.size >= 128) children.delete(children.keys().next().value)
    children.set(session, { parent: data.parentID, agent: safeIdentifier(data.agent), running: false })
    return
  }
  if (!known(session)) return
  const child = children.get(session)
  const correlation = safeIdentifier(data.assistantMessageID)
  if (event.type === "session.step.started") {
    const provider = safeIdentifier(data.model?.providerID)
    const model = safeIdentifier(data.model?.id || data.model?.modelID)
    if (correlation) {
      if (steps.size >= 256) steps.delete(steps.keys().next().value)
      steps.set(correlation, { provider, model })
    }
    nexusEvent(cwd, session, "session.resumed")
    const currentAgent = safeIdentifier(data.agent)
    if (!activeAgents.has(session) && currentAgent) {
      if (activeAgents.size >= 128) activeAgents.delete(activeAgents.keys().next().value)
      activeAgents.set(session, currentAgent)
      if (!child) nexusEvent(cwd, session, "agent.started", { agent: currentAgent, correlation: session })
    }
    if (child && !child.running) {
      const agent = currentAgent || child.agent
      if (agent) {
        child.agent = agent
        child.running = true
        nexusEvent(cwd, child.parent, "agent.started", { agent, correlation: session,
          parent: children.get(child.parent)?.agent || activeAgents.get(child.parent) })
      }
    }
    if (model || provider) nexusEvent(cwd, session, "model.reported", { model, provider, correlation })
  } else if (event.type === "session.step.ended") {
    const fields = { ...steps.get(correlation), correlation }
    steps.delete(correlation)
    for (const [key, value] of [["input_tokens", data.tokens?.input], ["output_tokens", data.tokens?.output], ["cost", data.cost]]) {
      if (Number.isFinite(value) && value >= 0) fields[key] = value
    }
    nexusEvent(cwd, session, "model.reported", fields)
  } else if (event.type === "session.skill.activated") {
    const skill = safeIdentifier(data.name)
    if (skill) nexusEvent(cwd, session, "skill.loaded", { skill, correlation: safeIdentifier(data.id) })
  } else if (event.type === "permission.replied" && data.reply === "reject") {
    nexusEvent(cwd, session, "security.denied", { correlation: safeIdentifier(data.requestID) })
  } else if (event.type === "session.deleted" || event.type?.startsWith("session.execution.")) {
    const terminal = event.type.split(".").at(-1)
    nexusEvent(cwd, session, terminal === "deleted" ? "session.ended" : "session.idle")
    const agent = activeAgents.get(session)
    if (agent && !child && terminal !== "deleted") {
      nexusEvent(cwd, session, terminal === "succeeded" ? "agent.completed" : "agent.failed", { agent, correlation: session })
    }
    activeAgents.delete(session)
    if (child?.running && ["succeeded", "failed", "interrupted"].includes(terminal)) {
      nexusEvent(cwd, child.parent, terminal === "succeeded" ? "agent.completed" : "agent.failed",
        { agent: child.agent, correlation: session })
      child.running = false
    }
    if (terminal === "deleted") children.delete(session)
  }
}

async function observeV2(ctx, cwd) {
  const children = new Map()
  const steps = new Map()
  const activeAgents = new Map()
  const pendingSkills = new Map()
  const lastMcpCheck = new Map()
  await ctx.tool?.hook("execute.before", (event) => toolObservation(cwd, event, "started", children, pendingSkills))
  await ctx.tool?.hook("execute.after", (event) => {
    if (event.status === "completed" || event.status === "error") {
      toolObservation(cwd, event, event.status === "completed" ? "completed" : "failed", children, pendingSkills)
    }
  })
  await ctx.session.hook("model.request", (event) => {
    if (event.kind !== "primary") return
    const session = event.sessionID
    if (!nexusSinks.get(cwd)?.sessions.has(session) && !children.has(session)) return
    const model = safeIdentifier(event.model?.id)
    const provider = safeIdentifier(event.model?.providerID)
    nexusEvent(cwd, session, "session.heartbeat")
    if (model || provider) nexusEvent(cwd, session, "model.reported", { model, provider })
    if (typeof ctx.mcp?.list === "function" && Date.now() - (lastMcpCheck.get(session) || 0) > 15000) {
      if (lastMcpCheck.size >= 128) lastMcpCheck.delete(lastMcpCheck.keys().next().value)
      lastMcpCheck.set(session, Date.now())
      void reportMcp(ctx, cwd, session)
    }
  })
  if (typeof ctx.event?.subscribe === "function") {
    const controller = new AbortController()
    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({ signal: controller.signal,
          types: ["session.created", "session.deleted", "session.step.started", "session.step.ended", "session.skill.activated",
            "session.execution.succeeded", "session.execution.failed", "session.execution.interrupted", "permission.replied"] })) {
          observeRuntime(cwd, event, children, steps, activeAgents)
        }
      } catch (error) { debug(error) }
    })()
    return () => controller.abort()
  }
}

async function reportMcp(ctx, cwd, session) {
  try {
    const result = await ctx.mcp.list()
    const entries = Array.isArray(result) ? result : result?.data
    if (!Array.isArray(entries)) return
    for (const item of entries.slice(0, 64)) {
      const server = safeIdentifier(item?.name)
      if (!server) continue
      const status = typeof item.status === "string" ? item.status : item.status?.type || item.status?.status
      const observed = ["connected", "pending", "disabled", "failed", "needs_auth"].includes(status) ? status : undefined
      nexusEvent(cwd, session, "mcp.reported", { server, connection_status: observed })
    }
  } catch (error) { debug(error) }
}

async function resolveBlock(event, cwd) {
  const key = sessionKey(event)
  // The context event carries the transcript, not the prompt draft, so the
  // task comes from the messages when they are readable and from the entry the
  // prompt hook warmed otherwise.
  const task = taskText(event && event.prompt && event.prompt.text) || latestUserTask(event && event.messages)
  if (task) return await activationFor(key, task, cwd, key)
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
      if (!known || known.task !== task) activationFor(key, task, cwd, key)
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

  // A compacted session must re-read the current map and work state instead of
  // reusing the block that was valid before compaction.
  try {
    if (typeof ctx.session.hook === "function") {
      await ctx.session.hook("compaction", (event) => {
        activations.delete(sessionKey(event))
      })
    }
  } catch {
    // The host may not expose compaction; context epoch still invalidates.
  }
}

async function serveV1(input, output, cwd) {
  try {
    const task = taskText(lastUserText(input))
    if (!task) return
    const block = await activationFor(sessionKey(input), task, cwd, sessionKey(input))
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
  // Exposed on the definition, not as a named export: a V1 host treats every
  // exported function as a plugin.
  resolveEngine: locateEngine,
  async setup(ctx) {
    debugStatus("registering OpenCode hooks")
    await install(ctx)
    try {
      return await observeV2(ctx, (ctx?.location?.directory) || process.cwd())
    } catch (error) { debug(error) }
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


