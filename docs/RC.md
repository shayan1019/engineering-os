# EOS RC — Evidence Trust, Run Identity and Universal Discovery

Date: 2026-11-21
Base: `eos/benchmark-0.6.1` @ `6bfaaf0870ed`
Decision: `edr-20261121T104937-b8e749` (STANDARD)

The release candidate does not add features for their own sake. It closes the
three measurement claims that were still unproven.

## 1. Evidence trust is now three separate states

| State | Question | How it is proven |
| --- | --- | --- |
| Integrity | Was the file changed after it was written? | gate: log SHA-256; P5: `artifact_sha256` |
| Content match | Does the gate JSON agree with its log? Does the P5 result agree with its artifact? | gate: log header + every command argv/rc/timeout + failure names; P5: `content_sha256` recomputed |
| Provenance | Who produced it? | **UNSIGNED** — no signing key is configured |

`VERIFIED` means integrity **and** content match. It does **not** mean trusted
authorship. The report carries `trusted_provenance: false` and a note.

Negative tests (P6 selftest, 17/17):

- a gate JSON `verdict` changed from FAIL to PASS while the log still says FAIL
  → `content_mismatch`, overall `PARTIAL`;
- a log edited after the artifact was written → `tampered`;
- a corrupt JSON → `corrupt`;
- a P5 scorecard edited without re-sealing → `tampered`;
- an author who recomputes both P5 hashes → integrity passes, but provenance
  stays `UNSIGNED`; the report never claims trust.

Known limit: without a signing key, a coordinated rewrite of artifact + hashes
cannot be attributed. That is why provenance is a separate, explicit state.

## 2. Real runs are identified by `run_id`, not by result

The 0.6.1 reader de-duplicated by result hash. That conflated two genuine runs
that produced the same numbers. 0.6.2 adds `run_id` to every P5 artifact:

- two real runs with identical results are **two runs**;
- only a copied file (same `run_id`) is a `copies_ignored`;
- result identity (`content_sha256`) is still used for content verification.

Latest report: **4 verified P5 runs, 0 copies ignored, 6 legacy artifacts
excluded from trends**.

## 3. Universal project discovery

`infer_project` now discovers an unregistered folder when:

- there is no workspace identity, no profile and no `.eos/project.json`;
- the folder contains a project marker (`pyproject.toml`, `package.json`,
  `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `composer.json`,
  `Gemfile`, `mix.exs`, `requirements.txt`, `Makefile`, `*.csproj`, `*.sln`).

It returns `auto-<folder>`. `project_map` resolves that id to the workspace
root without an explicit path, so the first preflight can create the map and
the write gate can authorise the folder.

Verified mechanically:

- a temp folder with only `package.json` → `auto-<folder>`, map created with no
  `--project-path`, gate refuses without a decision and names the reason;
- an empty folder → **not** invented as a project;
- the existing workspace keeps its `.eos/project.json` identity, so this
  workspace is not reclassified.

**Not verified:** a real host session opened in a brand-new folder with no EOS
command. The mechanism is tested; the host journey is not. This is the main
remaining discovery claim.

## 4. Real project pilots (read-only)

From 0.6.1, unchanged: `AURA` (72 sources, 229 edges) and `vpniran`
(330 sources, 336 edges). Both Python-heavy. A non-Python real-project host
test is still missing; the web fixture (`index.html` + `app.js`) is synthetic.

## 5. P5-C and P5-D

- P5-C protocol is documented (`docs/P5C_PROTOCOL.md`). A runnable runner is
  **NOT BUILT**. Provider access is still 402, so no model result is claimed.
- P5-D: only OpenCode has a verified `FULL_GUARD` path on this machine. Codex
  and Claude Code are `CONTEXT_ONLY`, BLACKBOX is `ADAPTER_ONLY`. No new host
  evidence was produced in this release.

## 6. P6 improvement loop

Proposals now carry `last_seen`, `still_active`, `affected_suites`,
`affected_baselines` and `affected_versions`. A failure that is not present in
the latest gate run is marked inactive and produces **no** proposal, while it
stays visible under `historical_failures`. No proposal is auto-applied.

## 7. Verdict

| Claim | Status |
| --- | --- |
| P6 rejects a tampered gate verdict | **PASS** (17/17 selftest) |
| P6 separates integrity / content / provenance | **PASS** |
| Real runs are not merged by equal results | **PASS** (`run_id`) |
| Unregistered folder discovery without a path | **PASS (mechanical)** |
| New host session in a new folder, no command | **NOT_TESTED** |
| Non-Python real project host test | **NOT_TESTED** |
| P5-C runner | **NOT BUILT** |
| P5-C model benchmark | **BLOCKED (402)** |
| P5-D Codex / Claude / BLACKBOX | **NOT_TESTED** |
| P6 closed-loop improvement | **PARTIAL: proposals, no automatic change** |

## 8. Evidence

- `.agents/evidence/gates/eos/gate-p6-20261121T105550Z.json` (selftest 17/17, report VERIFIED)
- `.agents/evidence/gates/eos/gate-vnext_dev-20261121T105926Z.json` (15/15)
- `.agents/evidence/gates/eos/gate-p5-20261121T105539Z.json` (18/18)
- `.agents/evidence/p6/metrics-20261121T105550Z.json`
- `.agents/evidence/map/aura/PROJECT_MAP.json`, `.agents/evidence/map/vpniran/PROJECT_MAP.json`
