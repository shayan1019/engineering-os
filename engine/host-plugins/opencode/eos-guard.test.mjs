// Contract tests for the EOS write guard. These are pure functions: no host,
// no model, no filesystem writes. The live host hook test lives in
// engine/tools/host_capability.py because only the real host can prove that a
// hook is actually invoked.
import test from "node:test"
import assert from "node:assert/strict"
import { classifyShell, summaryOf, permissionDecision } from "./eos-guard.js"

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
  ]) {
    const verdict = classifyShell(command)
    assert.equal(verdict.allow, true, command + " -> " + verdict.reason)
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
    "npm run build",
    "pip install requests",
    "powershell -Command New-Item x",
    "cmd /c del x",
    "Invoke-Expression $payload",
    "rm -rf build",
  ]) {
    const verdict = classifyShell(command)
    assert.equal(verdict.allow, false, command)
  }
})

test("a read-only command with redirection is still a write", () => {
  assert.equal(classifyShell("pytest -q > report.txt").allow, false)
  assert.equal(classifyShell("git diff > patch.diff").allow, false)
})

test("unknown commands fail closed", () => {
  assert.equal(classifyShell("some-tool --do-magic").allow, false)
  assert.equal(classifyShell("").allow, false)
})

test("allowlisted tools cannot write arbitrary paths", () => {
  for (const command of [
    "python .agents/tools/impact.py --project eos --task x --out .opencode/plugin/eos-guard.js",
    "python .agents/tools/graph_freshness.py invalidate --project eos --confirm",
    "python .agents/tools/pytest_delta.py --project eos --suite unit --baseline x.json -- python_ai/tests",
    "python .agents/tools/team.py run --plan p.json --worker-cmd '[\"python\",\"x.py\"]'",
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
    action: "shell", effect: "allow", resources: ["git status --short"],
  })
  assert.equal(allow.allow, true)
  const deny = permissionDecision({
    action: "shell", effect: "allow",
    resources: ["Set-Content -Path x.txt -Value OK"],
  })
  assert.equal(deny.allow, false)
  assert.equal(deny.kind, "shell")
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
