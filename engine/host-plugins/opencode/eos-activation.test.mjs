/**
 * Test for the EOS activation plugin.
 *
 * The plugin is the "no Chief Architect dependency" mechanism: the host calls
 * it on its own chat.params lifecycle event and EOS answers with the project,
 * the rules, the relevant skills, the specialist set and the gate.
 *
 * The test loads the plugin, feeds it a fake session and a fake `eos` command,
 * and asserts that:
 *   1. the plugin injects the context block into the system prompt,
 *   2. it injects nothing when the engine is not available,
 *   3. it never touches the tool list or the permissions.
 *
 * Run: node engine/host-plugins/opencode/eos-activation.test.mjs
 */
import { mkdtempSync, writeFileSync, chmodSync, rmSync } from "node:fs"
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

function fakeEosBin(dir, body) {
  // A fake engine that prints the given context block and ignores its
  // arguments, exactly like the real tool does with --task-file. The plugin is
  // told about it through EOS_ACTIVATE_COMMAND, so the test never depends on
  // an installed EOS.
  const file = join(dir, "context.txt")
  writeFileSync(file, body, "utf8")
  const script = join(dir, "fake-engine.mjs")
  writeFileSync(
    script,
    `import { readFileSync } from "node:fs"\n` +
      `const file = ${JSON.stringify(file)}\n` +
      `process.stdout.write(readFileSync(file, "utf8"))\n`,
    "utf8",
  )
  return ["node", script]
}

async function loadPlugin() {
  const mod = await import("file://" + pluginPath.replace(/\\/g, "/") + "?v=" + Date.now())
  return mod.default
}

function sessionInput(text) {
  return { message: { parts: [{ type: "text", text }] } }
}

async function main() {
  const originalPath = process.env.PATH
  const originalCommand = process.env.EOS_ACTIVATE_COMMAND

  // 1. the engine answers -> the block is injected
  {
    const dir = mkdtempSync(join(tmpdir(), "eos-plugin-"))
    const block = "<eos-context>project: bighero | risk: high</eos-context>"
    process.env.EOS_ACTIVATE_COMMAND = JSON.stringify(fakeEosBin(dir, block))
    const plugin = await loadPlugin()
    const hooks = await plugin({ directory: dir, worktree: dir })
    const output = { system: ["existing system prompt"] }
    await hooks["chat.params"](sessionInput("BigHero'da entry bug var"), output)
    process.env.EOS_ACTIVATE_COMMAND = originalCommand
    process.env.PATH = originalPath
    rmSync(dir, { recursive: true, force: true })

    check("the plugin registers the chat.params hook", typeof hooks["chat.params"] === "function")
    check(
      "the context block is injected into the system prompt",
      output.system.some((s) => s.includes("eos-context")),
      JSON.stringify(output.system),
    )
    check(
      "the existing system prompt is preserved",
      output.system[0] === "existing system prompt",
    )
    check("no tool is added", output.tools === undefined)
    check("no permission is added", output.permission === undefined)
  }

  // 2. the engine is missing -> nothing is injected, nothing breaks
  {
    const dir = mkdtempSync(join(tmpdir(), "eos-plugin-empty-"))
    process.env.EOS_ACTIVATE_COMMAND = JSON.stringify([join(dir, "no-such-engine")])
    const plugin = await loadPlugin()
    const hooks = await plugin({ directory: dir, worktree: dir })
    const output = { system: ["existing system prompt"] }
    let threw = false
    try {
      await hooks["chat.params"](sessionInput("something"), output)
    } catch {
      threw = true
    }
    process.env.EOS_ACTIVATE_COMMAND = originalCommand
    process.env.PATH = originalPath
    rmSync(dir, { recursive: true, force: true })

    check("a missing engine does not break the session", !threw)
    check("a missing engine injects nothing", output.system.length === 1)
  }

  // 3. an empty turn carries no task -> nothing is injected
  {
    const plugin = await loadPlugin()
    const hooks = await plugin({ directory: tmpdir(), worktree: tmpdir() })
    const output = { system: [] }
    await hooks["chat.params"](sessionInput("   "), output)
    check("an empty turn injects nothing", output.system.length === 0)
  }

  console.log(failures === 0 ? "\nplugin test: PASS" : `\nplugin test: FAIL (${failures})`)
  process.exit(failures === 0 ? 0 : 1)
}

main()
