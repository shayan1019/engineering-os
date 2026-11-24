// A large project is not a denied project. Hashing a few thousand files from a
// cold disk cache takes tens of seconds (measured: 32 s); the guard has to wait
// for the verdict instead of reporting its own impatience as a DENY.
import test from "node:test"
import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import guard from "./eos-guard.js"

const DELAY = 13000

async function host(verdicts) {
  const workspace = mkdtempSync(join(tmpdir(), "eos-slow-"))
  const tools = join(workspace, ".agents", "tools")
  mkdirSync(tools, { recursive: true })
  // The "engine" is JavaScript run by node: no interpreter assumption.
  for (const [name, value] of Object.entries(verdicts)) {
    writeFileSync(join(tools, name),
      `setTimeout(() => console.log(JSON.stringify(${JSON.stringify(value)})), ${DELAY})\n`)
  }
  process.env.EOS_PYTHON = process.execPath
  const hooks = {}
  const hook = async (name, fn) => { hooks[name] = fn }
  await guard.setup({ location: { directory: workspace },
    session: { hook }, tool: { hook }, permission: { hook } })
  return { hooks, path: join(workspace, "a.txt") }
}

const write = (h) => h.hooks["execute.before"](
  { tool: "write", sessionID: "s", callID: "c" }, { args: { filePath: h.path } })

test("a slow ALLOW is an ALLOW", async () => {
  const h = await host({ "decision.py": {}, "write_gate.py": { allow: true, map_state: "CURRENT" } })
  await write(h)
})

test("a slow DENY is still a DENY", async () => {
  const h = await host({ "decision.py": {}, "work_state.py": { ok: true }, "write_gate.py":
    { allow: false, needs_decision: false, map_state: "STALE", reasons: ["map is STALE"] } })
  await assert.rejects(write(h), /EOS BLOCKED.*map is STALE/s)
})
