/**
 * Test for the EOS activation plugin.
 *
 * The plugin is the "no Chief Architect dependency" mechanism: the host asks it
 * for context on its own session lifecycle and EOS answers with the project,
 * the rules, the relevant skills, the specialist set and the gate.
 *
 * The test loads the plugin, gives it a fake host and a fake `eos` command, and
 * asserts that:
 *   1. the default export is a host definition (id + setup, and a V1 server()),
 *   2. the block is injected into the system prompt and nothing else changes,
 *   3. one user turn activates EOS once, not once per model step,
 *   4. a resumed session (no prompt hook) still activates from its transcript,
 *   5. a missing engine and an empty turn inject nothing and break nothing.
 *
 * Run: node engine/host-plugins/opencode/eos-activation.test.mjs
 */
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
const pluginPath = join(here, "eos-activation.js")

let failures = 0
function check(name, condition, detail = "") {
  if (condition) {
    console.log(`PASS ${name}`)
  } else {
    failures += 1
    console.log(`FAIL ${name}${detail ? " — " + detail : ""}`)
  }
}

/**
 * A fake engine: prints the given context block, and records one run per
 * invocation. The plugin is told about it through EOS_ACTIVATE_COMMAND, so the
 * test never depends on an installed EOS.
 */
function fakeEngine(dir, body) {
  const content = join(dir, "context.txt")
  writeFileSync(content, body, "utf8")
  const runs = join(dir, "runs.txt")
  const script = join(dir, "fake-engine.mjs")
  writeFileSync(
    script,
    `import { appendFileSync, readFileSync } from "node:fs"\n` +
      `appendFileSync(${JSON.stringify(runs)}, "x")\n` +
      `process.stdout.write(readFileSync(${JSON.stringify(content)}, "utf8"))\n`,
    "utf8",
  )
  return {
    command: ["node", script],
    runs() {
      try {
        return readFileSync(runs, "utf8").length
      } catch {
        return 0
      }
    },
  }
}

let loads = 0
async function loadPlugin() {
  loads += 1
  const spec = "file://" + pluginPath.replace(/\\/g, "/") + "?v=" + loads + "-" + Date.now()
  const mod = await import(spec)
  return mod.default
}

/** The smallest host surface the plugin is allowed to depend on. */
function fakeHost(directory) {
  const hooks = {}
  return {
    hooks,
    ctx: {
      location: { directory, project: { id: "test", directory, canonical: directory } },
      session: {
        async hook(name, callback) {
          hooks[name] = callback
          return {
            dispose: async () => {
              delete hooks[name]
            },
          }
        },
      },
    },
  }
}

function promptEvent(text) {
  return { sessionID: "s1", prompt: { text, files: [], agents: [], skills: [] }, metadata: {}, delivery: "steer" }
}

function contextEvent(system, messages = []) {
  return { sessionID: "s1", agent: "build", system, messages, tools: {}, options: {} }
}

function userMessage(text) {
  return { role: "user", content: [{ type: "text", text }] }
}

async function main() {
  const originalCommand = process.env.EOS_ACTIVATE_COMMAND

  // 1. the engine answers -> the block is injected into the system prompt
  {
    const dir = mkdtempSync(join(tmpdir(), "eos-plugin-"))
    const block = "<eos-context>project: demo | risk: high</eos-context>"
    process.env.EOS_ACTIVATE_COMMAND = JSON.stringify(fakeEngine(dir, block).command)
    const plugin = await loadPlugin()

    check("the definition carries an id", typeof plugin.id === "string" && plugin.id.length > 0)
    check("the definition carries a setup function", typeof plugin.setup === "function")
    check("the V1 entrypoint is still offered", typeof plugin.server === "function")

    const host = fakeHost(dir)
    await plugin.setup(host.ctx)
    check("the prompt hook is registered", typeof host.hooks.prompt === "function")
    check("the context hook is registered", typeof host.hooks.context === "function")

    await host.hooks.prompt(promptEvent("demo project entry bug"))
    const event = contextEvent([{ type: "text", text: "existing system prompt" }])
    await host.hooks.context(event)

    check(
      "the context block is injected into the system prompt",
      event.system.some((part) => typeof part.text === "string" && part.text.includes("eos-context")),
      JSON.stringify(event.system),
    )
    check(
      "the existing system prompt is preserved",
      event.system[0].text === "existing system prompt",
    )
    check("no tool is added", Object.keys(event.tools).length === 0)
    check("no permission is added", event.permission === undefined)
    check("no request option is overridden", Object.keys(event.options).length === 0)

    delete process.env.EOS_ACTIVATE_COMMAND
    rmSync(dir, { recursive: true, force: true })
  }

  // 2. one turn with several model steps -> the router runs once
  {
    const dir = mkdtempSync(join(tmpdir(), "eos-plugin-once-"))
    const engine = fakeEngine(dir, "<eos-context>once</eos-context>")
    process.env.EOS_ACTIVATE_COMMAND = JSON.stringify(engine.command)
    const plugin = await loadPlugin()
    const host = fakeHost(dir)
    await plugin.setup(host.ctx)

    await host.hooks.prompt(promptEvent("indicator RE formatini kontrol et"))
    await host.hooks.context(contextEvent([]))
    await host.hooks.context(contextEvent([]))
    await host.hooks.context(contextEvent([]))

    check("one user turn activates EOS once", engine.runs() === 1, `runs=${engine.runs()}`)

    await host.hooks.prompt(promptEvent("baska bir gorev"))
    await host.hooks.context(contextEvent([]))
    check("a new turn activates again", engine.runs() === 2, `runs=${engine.runs()}`)

    delete process.env.EOS_ACTIVATE_COMMAND
    rmSync(dir, { recursive: true, force: true })
  }

  // 3. a resumed session has no prompt hook -> the transcript activates it
  {
    const dir = mkdtempSync(join(tmpdir(), "eos-plugin-resume-"))
    const engine = fakeEngine(dir, "<eos-context>resumed</eos-context>")
    process.env.EOS_ACTIVATE_COMMAND = JSON.stringify(engine.command)
    const plugin = await loadPlugin()
    const host = fakeHost(dir)
    await plugin.setup(host.ctx)

    const event = contextEvent([], [userMessage("devam eden gorev"), { role: "assistant", content: [] }])
    await host.hooks.context(event)

    check(
      "a resumed session still activates from its transcript",
      event.system.some((part) => part.text.includes("resumed")),
      JSON.stringify(event.system),
    )

    delete process.env.EOS_ACTIVATE_COMMAND
    rmSync(dir, { recursive: true, force: true })
  }

  // 4. the engine is missing -> nothing is injected, nothing breaks
  {
    const dir = mkdtempSync(join(tmpdir(), "eos-plugin-empty-"))
    process.env.EOS_ACTIVATE_COMMAND = JSON.stringify([join(dir, "no-such-engine")])
    const plugin = await loadPlugin()
    const host = fakeHost(dir)
    await plugin.setup(host.ctx)

    let threw = false
    const event = contextEvent([{ type: "text", text: "existing system prompt" }])
    try {
      await host.hooks.prompt(promptEvent("something"))
      await host.hooks.context(event)
    } catch {
      threw = true
    }

    delete process.env.EOS_ACTIVATE_COMMAND
    rmSync(dir, { recursive: true, force: true })

    check("a missing engine does not break the session", !threw)
    check("a missing engine injects nothing", event.system.length === 1, JSON.stringify(event.system))
  }

  // 5. an empty turn carries no task -> nothing is injected, nothing is run
  {
    const dir = mkdtempSync(join(tmpdir(), "eos-plugin-blank-"))
    const engine = fakeEngine(dir, "<eos-context>should-not-appear</eos-context>")
    process.env.EOS_ACTIVATE_COMMAND = JSON.stringify(engine.command)
    const plugin = await loadPlugin()
    const host = fakeHost(dir)
    await plugin.setup(host.ctx)

    await host.hooks.prompt(promptEvent("   "))
    const event = contextEvent([])
    await host.hooks.context(event)

    check("an empty turn injects nothing", event.system.length === 0)
    check("an empty turn runs no engine", engine.runs() === 0, `runs=${engine.runs()}`)

    delete process.env.EOS_ACTIVATE_COMMAND
    rmSync(dir, { recursive: true, force: true })
  }

  // 6. a V1 host still gets a working chat.params hook
  {
    const dir = mkdtempSync(join(tmpdir(), "eos-plugin-v1-"))
    process.env.EOS_ACTIVATE_COMMAND = JSON.stringify(fakeEngine(dir, "<eos-context>v1</eos-context>").command)
    const plugin = await loadPlugin()
    const hooks = await plugin.server({ directory: dir, worktree: dir })
    const output = { system: ["existing system prompt"] }
    await hooks["chat.params"]({ message: { parts: [{ type: "text", text: "demo entry bug" }] } }, output)

    delete process.env.EOS_ACTIVATE_COMMAND
    rmSync(dir, { recursive: true, force: true })

    check("the V1 hook injects the block", output.system.some((s) => s.includes("eos-context")))
    check("the V1 hook preserves the system prompt", output.system[0] === "existing system prompt")
  }

  process.env.EOS_ACTIVATE_COMMAND = originalCommand
  console.log(failures === 0 ? "\nplugin test: PASS" : `\nplugin test: FAIL (${failures})`)
  process.exit(failures === 0 ? 0 : 1)
}

main()
