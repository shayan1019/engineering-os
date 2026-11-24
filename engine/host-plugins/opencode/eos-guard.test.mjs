// Contract tests for the EOS write guard. These are pure functions: no host,
// no model, no filesystem writes. The live host hook test lives in
// engine/tools/host_capability.py because only the real host can prove that a
// hook is actually invoked.
import test from "node:test"
import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, writeFileSync, readFileSync, existsSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import guard, { classifyShell, summaryOf, permissionDecision, createWriteQueue, skillFromReadOutput, skillFromPath, readTarget } from "./eos-guard.js"

test("read-only shell commands are allowed", () => {
  for (const command of [
    "git status --short",
    "git diff HEAD",
    "git status 2>&1",
    "pytest python_ai/tests -q",
    "python -m pytest tests -q",
    "python .agents/tools/quality_gate.py verify --file x.json",
    "python .agents/tools/decision.py status --id edr-x",
    "python .agents/tools/decision.py open --project eos --task x --risk ATOMIC --scope a.py --approve",
    "python .agents/tools/auto_decision.py prepare --task x --scope a.py --approve",
    "python .agents/tools/team.py selftest",
    "python .agents\\tools\\decision.py open --project eos --task x --risk ATOMIC --scope a.py --approve",
    "python .agents/tools/impact.py --project eos --task \"a -> b\"",
    "python .agents/tools/challenge.py check --id chl-x",
    "python .agents/tools/completion.py check --project eos --task fix --decision edr-x",
    "python .agents/tools/ship.py plan --project eos --decision edr-x --branch eos/t1",
    "python .agents/tools/workspace.py resolve --path .",
    "python .agents/tools/tool_evidence.py check --require test",
    "python .agents/tools/model_probe.py invoke --model opencode/claude-fable-5-2",
    "eos install --local C:/proj/.agents --source C:/proj/engineering-os --force",
    "rg --files",
    "Get-Content .agents/registry/GATES.json",
    "cargo test --offline",
    "cmd /c echo shell-exec-ok",
    "cmd.exe /c echo shell-exec-ok",
    "Get-ChildItem -Force | Select-Object Mode,Name",
    "git status --short && git rev-parse HEAD",
  ]) {
    const verdict = classifyShell(command, process.cwd())
    assert.equal(verdict.allow, true, command + " -> " + verdict.reason)
  }
})

test("safe wrappers and quoted text are not false positives", () => {
  for (const command of [
    "cmd /c echo shell-exec-ok",
    "cmd.exe /c echo shell-exec-ok",
    'rg -n "Set-Content" engine',
    'rg -n "a > b" engine',
    "Get-ChildItem -Force | Select-Object Mode,Name",
    "git status --short && git rev-parse HEAD",
  ]) {
    assert.equal(classifyShell(command, process.cwd()).effect, "allow", command)
  }
})

test("safe test and build commands are allowed in the workspace", () => {
  for (const command of [
    "pytest -q",
    "python -m pytest tests -q",
    "cargo test",
    "cargo build",
    "npm test",
    "npm run build",
  ]) {
    assert.equal(classifyShell(command, process.cwd()).effect, "allow", command)
  }
})

test("shell writes and bypasses are refused", () => {
  for (const command of [
    "python -c \"open('x','w').write('pwn')\"",
    "python - <<'PY'\nopen('x','w').write('pwn')\nPY",
    "echo hi > target.txt",
    "Get-Content a | Set-Content b",
    "git commit -am x",
    "git push origin main",
    "git status; rm -rf build",
    "git status && python -c \"open('x','w')\"",
    "git status || del x",
    "npm install",
    "pip install requests",
    "powershell -Command New-Item x",
    "cmd /c del x",
    "Invoke-Expression $payload",
    "rm -rf build",
  ]) {
    const verdict = classifyShell(command, process.cwd())
    assert.equal(verdict.allow, false, command)
  }
})

test("risky shell commands ask instead of auto-denying", () => {
  for (const command of [
    "git commit -am x",
    "git push origin main",
    "Set-Content -Path a.txt -Value OK",
    "echo hi > a.txt",
    "Remove-Item a.txt",
    "del a.txt",
    "script.bat",
    "C:\\tools\\app.exe",
    "Start-Process -Verb RunAs cmd",
    "runas /user:Administrator cmd",
  ]) {
    assert.equal(classifyShell(command, process.cwd()).effect, "ask", command)
  }
})

test("protected EOS files are denied", () => {
  for (const command of [
    "Set-Content -Path .opencode/plugins/eos-guard.js -Value x",
    "Remove-Item .agents/tools/decision.py",
    "cmd /c copy evil.js .opencode/plugins/eos-guard.js",
    "echo x > .opencode/plugins/eos-activation.js",
  ]) {
    assert.equal(classifyShell(command, process.cwd()).effect, "deny", command)
  }
})

test("a read-only command with redirection is still a write", () => {
  assert.equal(classifyShell("pytest -q > report.txt", process.cwd()).effect, "ask")
  assert.equal(classifyShell("git diff > patch.diff", process.cwd()).effect, "ask")
})

test("unknown commands are not auto-allowed", () => {
  assert.equal(classifyShell("some-tool --do-magic").effect, "ask")
  assert.equal(classifyShell("").effect, "deny")
})

test("allowlisted tools cannot write arbitrary paths", () => {
  for (const command of [
    "python .agents/tools/impact.py --project eos --task x --out .opencode/plugin/eos-guard.js",
    "python .agents/tools/graph_freshness.py invalidate --project eos --confirm",
    "python .agents/tools/pytest_delta.py --project eos --suite unit --baseline x.json -- python_ai/tests",
    "python .agents/tools/team.py run --plan p.json --worker-cmd '[\"python\",\"x.py\"]'",
    "python .agents/tools/team.py run --plan p.json",
    "python .agents/tools/scope_install.py install --mode workspace --root C:/x --apply",
    "python .agents/tools/workspace.py scope set --root C:/x --mode workspace",
    "eos install --local C:/Windows --source C:/proj/engineering-os --force",
    "python .agents/tools/orchestrator_runtime.py run --provider ruflo --plan p.json",
  ]) {
    const verdict = classifyShell(command)
    assert.equal(verdict.allow, false, command)
  }
})

test("write target resolution uses the host field names", () => {
  assert.equal(summaryOf("write", { filePath: "a.py", content: "x" }).path, "a.py")
  assert.equal(summaryOf("edit", { path: "b.py" }).path, "b.py")
  assert.equal(summaryOf("patch", { file: "c.py" }).path, "c.py")
  assert.equal(summaryOf("write", { filePath: "a.py" }).bytes, 0)
})

test("evidence summary never carries file content", () => {
  const content = "API_KEY = 'do-not-log-me'"
  const summary = summaryOf("write", { filePath: "secret.py", content })
  const encoded = JSON.stringify(summary)
  assert.equal(encoded.includes("do-not-log-me"), false)
  assert.equal(summary.bytes, content.length)
})

test("permission input carries the shell command in resources", () => {
  const allow = permissionDecision({
    action: "shell", effect: "allow", resources: ["cmd /c echo shell-exec-ok"],
  })
  assert.equal(allow.allow, true)
  assert.equal(allow.effect, "allow")
  const ask = permissionDecision({
    action: "shell", effect: "allow",
    resources: ["Set-Content -Path x.txt -Value OK"],
  })
  assert.equal(ask.allow, false)
  assert.equal(ask.effect, "ask")
  assert.equal(ask.kind, "shell")
  const deny = permissionDecision({
    action: "shell", effect: "allow",
    resources: ["Set-Content -Path .opencode/plugins/eos-guard.js -Value x"],
  })
  assert.equal(deny.effect, "deny")
})

test("permission write is deferred to the decision engine", () => {
  const verdict = permissionDecision({
    action: "write", effect: "allow", resources: ["engine/tools/x.py"],
  })
  assert.equal(verdict.kind, "write")
  assert.equal(verdict.allow, null)
  assert.equal(verdict.resource, "engine/tools/x.py")
})

test("permission read is never denied by the guard", () => {
  const verdict = permissionDecision({ action: "read", resources: ["a.py"] })
  assert.equal(verdict.allow, true)
})

test("a shell permission with no command is denied", () => {
  for (const resources of [[], undefined, null]) {
    const verdict = permissionDecision({ action: "shell", effect: "allow", resources })
    assert.equal(verdict.allow, false, JSON.stringify(resources))
  }
})

test("the post-write queue pairs a tool call id exactly once", () => {
  const queue = createWriteQueue()
  queue.note("a.py", "s1", "call-1")
  queue.note("a.py", "s1", "call-2")
  const first = queue.take("s1", { path: "a.py" }, "call-2")
  assert.equal(first.callID, "call-2")
  const second = queue.take("s1", { path: "a.py" })
  assert.equal(second.callID, "call-1")
  assert.equal(queue.take("s1", { path: "a.py" }), null)
})

test("a call id is never matched across sessions", () => {
  const queue = createWriteQueue()
  queue.note("a.py", "s1", "call-9")
  assert.equal(queue.take("s2", { path: "a.py" }, "call-9"), null)
  assert.equal(queue.take("s1", { path: "a.py" }, "call-9").callID, "call-9")
})

test("an execute.after call id pairs with a permission entry that had none", () => {
  // OpenCode 2.3.2: the permission event carries the path but no call id; the
  // execute.after event carries a call id but no args. Without this fallback the
  // write is allowed and the post-write refresh is silently skipped.
  const queue = createWriteQueue()
  queue.note("a.py", "s1", "")
  const taken = queue.take("s1", { path: "" }, "call-after")
  assert.equal(taken.path, "a.py")
  assert.equal(queue.take("s1", { path: "" }, "call-after"), null)
})

async function harness(gateAllow) {
  const workspace = mkdtempSync(join(tmpdir(), "eos-guard-"))
  const tools = join(workspace, ".agents", "tools")
  mkdirSync(tools, { recursive: true })
  const log = join(workspace, "touch.log")
  writeFileSync(join(tools, "decision.py"),
    `console.log(JSON.stringify({ok:true,allow:true,decision:"edr-test",reasons:[]}))\n`)
  writeFileSync(join(tools, "write_gate.py"),
    `console.log(JSON.stringify({allow:${gateAllow},needs_decision:false,` +
    `map_state:"CURRENT",decision:"edr-test",risk:"ATOMIC",project:"demo",reasons:[]}))\n`)
  writeFileSync(join(tools, "post_write.py"),
    `require("fs").appendFileSync(process.env.EOS_TOUCH_LOG,` +
    `process.argv.slice(2).join(" ")+"\\n");` +
    `console.log(JSON.stringify({ok:true,project:"demo",map_state:"CURRENT"}))\n`)
  writeFileSync(join(tools, "work_state.py"),
    `require("fs").appendFileSync(process.env.EOS_WORK_LOG,` +
    `process.argv.slice(2).join(" ")+"\\n");` +
    `console.log(JSON.stringify({ok:true,status:"recorded",record_id:"rid-test"}))\n`)
  const previousPython = process.env.EOS_PYTHON
  const previousLog = process.env.EOS_TOUCH_LOG
  const previousWorkLog = process.env.EOS_WORK_LOG
  const workLog = join(workspace, "work.log")
  process.env.EOS_PYTHON = process.execPath
  process.env.EOS_TOUCH_LOG = log
  process.env.EOS_WORK_LOG = workLog
  const handlers = {}
  await guard.setup({
    location: { directory: workspace },
    session: { hook: async () => {} },
    tool: { hook: async (name, fn) => { handlers[name] = fn } },
    permission: { hook: async (name, fn) => { handlers["permission." + name] = fn } },
  })
  return {
    log,
    workLog,
    before: (input, output) => handlers["execute.before"](input, output),
    after: (input, output) => handlers["execute.after"](input, output),
    restore: () => {
      if (previousPython === undefined) delete process.env.EOS_PYTHON
      else process.env.EOS_PYTHON = previousPython
      if (previousLog === undefined) delete process.env.EOS_TOUCH_LOG
      else process.env.EOS_TOUCH_LOG = previousLog
      if (previousWorkLog === undefined) delete process.env.EOS_WORK_LOG
      else process.env.EOS_WORK_LOG = previousWorkLog
    },
  }
}

test("an allowed write touches the map only after the write executes", async () => {
  const host = await harness(true)
  try {
    const input = { tool: "write", sessionID: "s-post", callID: "c1" }
    const output = { args: { filePath: "a.py", content: "x = 1\n" } }
    await host.before(input, output)
    assert.equal(existsSync(host.log), false,
      "the map was touched before the write executed")
    await host.after(input, { output: "ok" })
    const text = readFileSync(host.log, "utf8")
    assert.match(text, /touch --json/)
    assert.match(text, /a\.py/)
  } finally {
    host.restore()
  }
})

test("a denied write never touches the map", async () => {
  const host = await harness(false)
  try {
    const input = { tool: "write", sessionID: "s-deny", callID: "c2" }
    const output = { args: { filePath: "b.py", content: "x = 1\n" } }
    await assert.rejects(() => host.before(input, output), /EOS BLOCKED/)
    await host.after(input, { output: "" })
    assert.equal(existsSync(host.log), false, "a denied write touched the map")
    assert.equal(existsSync(host.workLog), true, "a denied write left no blocked evidence")
    const blocked = readFileSync(host.workLog, "utf8")
    assert.doesNotMatch(blocked, /record-atomic|close/, "a denied write closed a task")
    assert.match(blocked, /record-blocked/, "a denied write did not record blocked evidence")
  } finally {
    host.restore()
  }
})

test("a failed write never touches the map or records work state", async () => {
  const host = await harness(true)
  try {
    const input = { tool: "write", sessionID: "s-fail", callID: "c4" }
    const output = { args: { filePath: "a.py", content: "x = 1\n" } }
    await host.before(input, output)
    await host.after(input, { error: { type: "write_failed" }, output: "" })
    assert.equal(existsSync(host.log), false, "a failed write touched the map")
    assert.equal(existsSync(host.workLog), false, "a failed write recorded work state")
  } finally {
    host.restore()
  }
})

test("a successful ATOMIC write records a closed work state", async () => {
  const host = await harness(true)
  try {
    const input = { tool: "write", sessionID: "s-work", callID: "c3" }
    const output = { args: { filePath: "a.py", content: "x = 1\n" } }
    await host.before(input, output)
    await host.after(input, { output: "ok" })
    const text = readFileSync(host.workLog, "utf8")
    assert.match(text, /record-atomic/)
    assert.match(text, /--decision edr-test/)
    assert.match(text, /--file a\.py/)
    assert.match(text, /--map-state CURRENT/)
  } finally {
    host.restore()
  }
})

test("a read of a real SKILL.md becomes skill evidence", () => {
  const root = mkdtempSync(join(tmpdir(), "eos-skill-read-"))
  const dir = join(root, ".agents", "skills", "demo")
  mkdirSync(dir, { recursive: true })
  const file = join(dir, "SKILL.md")
  writeFileSync(file, "---\nname: demo\n---\nbody\n", "utf8")
  const info = skillFromReadOutput(`Read file ${file}, lines 1-3\n---\n`, root)
  assert.equal(info.skill, "demo")
  assert.equal(info.path, ".agents/skills/demo/SKILL.md")
  assert.equal(info.sha256.length, 64)
  const relative = skillFromReadOutput("Read file .agents/skills/demo/SKILL.md, lines 1-3\n---\n", root)
  assert.equal(relative.skill, "demo")
  assert.equal(relative.path, ".agents/skills/demo/SKILL.md")
  assert.equal(skillFromPath(".agents/skills/demo/SKILL.md", root).skill, "demo")
  assert.equal(readTarget("read", { path: ".agents/skills/demo/SKILL.md" }), ".agents/skills/demo/SKILL.md")
  assert.equal(skillFromReadOutput(`Read file ${join(root, "a.py")}, lines 1-1\n`, root), null)
})

test("EOS engine tools are allowed by absolute path", () => {
  const verdict = classifyShell('python "C:\\eos\\engine\\tools\\recovery.py" diagnose --path a.py', "C:\\proj")
  assert.equal(verdict.effect, "allow")
})
