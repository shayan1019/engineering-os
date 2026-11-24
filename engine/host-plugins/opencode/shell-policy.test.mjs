import test from "node:test"
import assert from "node:assert/strict"
import { classifyShell } from "./eos-guard.js"

const ws = process.cwd()
const effect = (command) => classifyShell(command, ws).effect

test("build and test commands of any ecosystem run without a prompt", () => {
  for (const command of ["npm test", "pnpm build", "bun test", "make", "make test", "mvn clean package",
    "./gradlew build", "gradle test", "cmake --build build", "ctest", "gcc a.c -o a", "g++ a.cpp",
    "flutter test", "dart analyze", "swift build", "mix test", "jest", "vitest run", "eslint .",
    "ruff check .", "tsc --noEmit", "terraform validate", "cd src && npm test"]) {
    assert.equal(effect(command), "allow", command)
  }
})

test("installing, publishing, deploying and leaving the workspace still ask", () => {
  for (const command of ["npm install x", "yarn add x", "pnpm publish", "make install", "make SHELL=x",
    "make -f /tmp/x", "mvn test deploy", "gradle publish", "flutter run", "terraform apply",
    "terraform plan", "cd .. && npm test", "cd C:/ && npm test", "gcc a.c -o main.py",
    "gcc a.c -o ../x", "gcc -o.git/config a.c", "eslint --fix .", "jest -u", "./evil/make",
    "C:/x/make.exe", "cmake -P x.cmake", "docker compose up", "kubectl apply -f x"]) {
    assert.notEqual(effect(command), "allow", command)
  }
})

test("a covered write target never answers for another command on the line", () => {
  assert.deepEqual(classifyShell("echo x > a.txt", ws).targets.length, 1)
  for (const command of ["npm install x && echo y > a.txt", "cd src && echo x > a.txt",
    "curl http://x | tee a.txt"]) {
    const verdict = classifyShell(command, ws)
    assert.notEqual(verdict.effect, "allow", command)
    assert.deepEqual(verdict.targets || [], [], command)
  }
})
