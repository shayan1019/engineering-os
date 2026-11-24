// Which engine does a host plugin run? Exactly the one config.json activates.
// Every case runs against both plugins: activation and enforcement must never
// disagree about the live engine.
import test from "node:test"
import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs"
import { createHash } from "node:crypto"
import { tmpdir } from "node:os"
import { join, dirname } from "node:path"
import { fileURLToPath } from "node:url"
import guard, { resolveEngine as guardResolve } from "./eos-guard.js"
import activation from "./eos-activation.js"

const here = dirname(fileURLToPath(import.meta.url))
const sha = (text) => createHash("sha256").update(text).digest("hex").toUpperCase()
const resolvers = [["guard", (w) => guardResolve(w)], ["activation", (w) => activation.resolveEngine(w)]]

function makeVersion(home, version) {
  const dir = join(home, "versions", version)
  const tools = join(dir, "engine", "tools")
  mkdirSync(tools, { recursive: true })
  const entries = []
  for (const name of ["decision.py", "eos_activate.py"]) {
    const text = `# ${version} ${name}\n`
    writeFileSync(join(tools, name), text)
    entries.push({ rel: `engine/tools/${name}`, sha256: sha(text), size: text.length })
  }
  writeFileSync(join(dir, "install-manifest.json"),
    JSON.stringify({ schema: 1, version, runtime_mode: "system", entries }))
  const feed = entries.map((e) => `${e.rel.slice(7)}:${e.sha256}\n`).join("")
  return { version, dir, engine_manifest_sha256: sha(feed), runtime_mode: "system" }
}

function fixture(active, versions) {
  const home = mkdtempSync(join(tmpdir(), "eos-res-"))
  const records = versions.map((v) => makeVersion(home, v))
  const write = (value) => writeFileSync(join(home, "config.json"),
    typeof value === "string" ? value : JSON.stringify(value))
  write({ schema: 1, active_version: active, versions: records })
  return { home, records, write, workspace: mkdtempSync(join(tmpdir(), "eos-ws-")) }
}

function withEnv(values, fn) {
  const keys = ["EOS_HOME", "EOS_ENGINE", "EOS_PYTHON", "EOS_ACTIVATE_COMMAND"]
  const saved = Object.fromEntries(keys.map((k) => [k, process.env[k]]))
  for (const k of keys) delete process.env[k]
  Object.assign(process.env, values)
  const restore = () => {
    for (const k of keys) {
      if (saved[k] === undefined) delete process.env[k]
      else process.env[k] = saved[k]
    }
  }
  let out
  try { out = fn() } catch (error) { restore(); throw error }
  if (out && typeof out.then === "function") return out.finally(restore)
  restore()
  return out
}

for (const [name, resolveEngine] of resolvers) {
  const run = (f, env = {}) => withEnv({ EOS_HOME: f.home, ...env }, () => resolveEngine(f.workspace))

  test(`${name}: active_version wins over a newer directory`, () => {
    const f = fixture("1.0.2", ["1.0.2", "1.0.3"])
    const got = run(f)
    assert.equal(got.problem, undefined)
    assert.equal(got.engine, join(f.home, "versions", "1.0.2", "engine"))
    assert.equal(got.source, "active_version")
  })

  test(`${name}: 1.0.10 is selected although it sorts below 1.0.2`, () => {
    const f = fixture("1.0.10", ["1.0.2", "1.0.10"])
    assert.equal(run(f).engine, join(f.home, "versions", "1.0.10", "engine"))
  })

  test(`${name}: invalid state fails closed, never another version`, () => {
    const cases = {
      "missing config": (f) => rmSync(join(f.home, "config.json")),
      "corrupt config": (f) => f.write("{ not json"),
      "no active": (f) => f.write({ versions: f.records }),
      "unrecorded active": (f) => f.write({ active_version: "9.9.9", versions: f.records }),
      "duplicate record": (f) => f.write({ active_version: "1.0.2", versions: [...f.records, f.records[0]] }),
      "path traversal": (f) => f.write({ active_version: "../x", versions: [{ ...f.records[0], version: "../x" }] }),
      "record elsewhere": (f) => f.write({ active_version: "1.0.2", versions: [{ ...f.records[0], dir: f.workspace }] }),
      "missing manifest": (f) => rmSync(join(f.records[0].dir, "install-manifest.json")),
      "manifest of another version": (f) => writeFileSync(join(f.records[0].dir, "install-manifest.json"),
        readFileSync(join(f.records[1].dir, "install-manifest.json"))),
      "tampered manifest hash": (f) => f.write({ active_version: "1.0.2",
        versions: [{ ...f.records[0], engine_manifest_sha256: "00" }] }),
      "tampered engine": (f) => writeFileSync(join(f.records[0].dir, "engine", "tools", "decision.py"), "print(1)"),
      "missing engine dir": (f) => rmSync(join(f.records[0].dir, "engine"), { recursive: true }),
      "interrupted activation": (f) => {
        mkdirSync(join(f.home, "state"))
        writeFileSync(join(f.home, "state", "activation-txn.json"), "{}")
      },
    }
    for (const [label, breakIt] of Object.entries(cases)) {
      const f = fixture("1.0.2", ["1.0.2", "1.0.3"])
      breakIt(f)
      const got = run(f)
      assert.ok(got.problem, label)
      assert.equal(got.engine, "", label)
    }
  })

  test(`${name}: project-local .agents precedes EOS_ENGINE and the global engine`, () => {
    const f = fixture("1.0.2", ["1.0.2"])
    const tools = join(f.workspace, ".agents", "tools")
    mkdirSync(tools, { recursive: true })
    writeFileSync(join(tools, "decision.py"), "")
    writeFileSync(join(tools, "eos_activate.py"), "")
    const got = run(f, { EOS_ENGINE: join(f.records[0].dir, "engine") })
    assert.equal(got.engine, join(f.workspace, ".agents"))
    assert.equal(got.source, "workspace")
  })

  test(`${name}: EOS_ENGINE is explicit and must be a real engine`, () => {
    const f = fixture("1.0.2", ["1.0.2", "1.0.3"])
    const engine = join(f.records[1].dir, "engine")
    const got = run(f, { EOS_ENGINE: engine })
    assert.equal(got.engine, engine)
    assert.equal(got.source, "EOS_ENGINE")
    assert.ok(run(f, { EOS_ENGINE: join(f.home, "nope") }).problem)
  })
}

test("both plugins carry the identical resolver", () => {
  const block = (file) => readFileSync(join(here, file), "utf8").replace(/\r/g, "")
    .match(/\/\/ ENGINE-RESOLVER BEGIN[\s\S]*?\/\/ ENGINE-RESOLVER END/)[0]
  assert.equal(block("eos-guard.js"), block("eos-activation.js"))
})

test("guard denies a write and activation reports UNAVAILABLE on a corrupt config", async () => {
  const f = fixture("1.0.2", ["1.0.2", "1.0.3"])
  f.write("{")
  await withEnv({ EOS_HOME: f.home }, async () => {
    const host = () => {
      const hooks = {}
      const hook = async (n, fn) => { hooks[n] = fn }
      return { hooks, ctx: { location: { directory: f.workspace },
        session: { hook }, tool: { hook }, permission: { hook } } }
    }
    const g = host()
    await guard.setup(g.ctx)
    await assert.rejects(g.hooks["execute.before"]({ tool: "write", sessionID: "s", callID: "c" },
      { args: { filePath: join(f.workspace, "a.txt") } }), /EOS BLOCKED.*config/s)
    const a = host()
    await activation.setup(a.ctx)
    const event = { sessionID: "s", system: [], messages: [{ role: "user", text: "corrupt config task" }] }
    await a.hooks.context(event)
    assert.match(event.system[0].text, /UNAVAILABLE/)
    assert.match(event.system[0].text, /config/)
  })
})

test("a running session follows a rollback without a restart", () => {
  const f = fixture("1.0.3", ["1.0.2", "1.0.3"])
  withEnv({ EOS_HOME: f.home }, () => {
    assert.match(guardResolve(f.workspace).engine, /1\.0\.3/)
    f.write({ schema: 1, active_version: "1.0.2", versions: f.records })
    assert.match(guardResolve(f.workspace).engine, /1\.0\.2/)
  })
})
