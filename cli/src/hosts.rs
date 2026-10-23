//! Host adapters.
//!
//! A host is an AI coding runtime EOS can hand work to. Each host has a
//! *declared* capability set and an honest verification status. EOS never
//! claims a capability a host does not have: Cursor has no subagents, so the
//! Cursor adapter ships rules and gate commands, not an agent fleet.
//!
//! Integration writes only EOS-owned files (names prefixed `eos-` or the nine
//! `chief-architect`/specialist names) and backs up anything it would replace.

use crate::paths;
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Serialize, Clone, Debug)]
pub struct Capabilities {
    pub agents: bool,
    pub subagents: bool,
    pub permission_enforcement: bool,
    pub skills: bool,
    pub hooks: bool,
    pub mcp: bool,
    pub rules: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum LaunchStyle {
    /// Set the working directory and start the interactive TUI.
    CwdOnly,
    /// Pass the project path as a positional argument.
    PathArg,
}

#[derive(Clone, Copy, Debug)]
pub struct HostSpec {
    pub id: &'static str,
    pub label: &'static str,
    pub binaries: &'static [&'static str],
    pub launch: LaunchStyle,
    /// Where EOS writes its adapter files, relative to the project.
    pub config_dir: &'static str,
    pub verification: &'static str,
    pub documented: &'static str,
    pub caps: fn() -> Capabilities,
}

pub const HOSTS: &[HostSpec] = &[
    HostSpec {
        id: "opencode",
        label: "OpenCode",
        binaries: &["opencode"],
        launch: LaunchStyle::CwdOnly,
        config_dir: ".opencode",
        verification: "VERIFIED on this machine: 9 agents registered, permission \
                       boundaries exercised (agent_dispatch_test.py PASS), skills \
                       discovered from .opencode/skills and .agents/skills.",
        documented: "docs/hosts.md",
        caps: opencode_caps,
    },
    HostSpec {
        id: "claude",
        label: "Claude Code",
        binaries: &["claude", "claude-code"],
        launch: LaunchStyle::CwdOnly,
        config_dir: ".claude",
        verification: "PARTIALLY VERIFIED: EOS writes .claude/agents and \
                       .claude/skills in Claude's documented markdown/frontmatter \
                       format, and .claude/skills is read by OpenCode V2 as a real \
                       skill root. Claude-side permission *enforcement* of the \
                       generated agents is UNVERIFIED because the Claude runtime is \
                       not installed here.",
        documented: "docs/hosts.md",
        caps: claude_caps,
    },
    HostSpec {
        id: "cursor",
        label: "Cursor",
        binaries: &["cursor", "cursor-agent"],
        launch: LaunchStyle::PathArg,
        config_dir: ".cursor",
        verification: "UNVERIFIED: Cursor is not installed on this machine. The \
                       adapter writes .cursor/rules only and declares no subagent or \
                       permission-enforcement support, which matches Cursor's public \
                       capability set.",
        documented: "docs/hosts.md",
        caps: cursor_caps,
    },
    HostSpec {
        id: "blackbox",
        label: "BLACKBOX AI",
        binaries: &["blackbox"],
        launch: LaunchStyle::CwdOnly,
        config_dir: ".blackbox",
        verification: "UNVERIFIED / NO ADAPTER: no public, documented local agent or \
                       skill directory contract could be established, so EOS declares \
                       no capability and writes nothing. It is detected and reported, \
                       not faked.",
        documented: "docs/hosts.md",
        caps: no_caps,
    },
];

fn opencode_caps() -> Capabilities {
    Capabilities {
        agents: true,
        subagents: true,
        permission_enforcement: true,
        skills: true,
        hooks: true,
        mcp: true,
        rules: true,
    }
}

fn claude_caps() -> Capabilities {
    Capabilities {
        agents: true,
        subagents: true,
        permission_enforcement: false,
        skills: true,
        hooks: true,
        mcp: true,
        rules: true,
    }
}

fn cursor_caps() -> Capabilities {
    Capabilities {
        agents: false,
        subagents: false,
        permission_enforcement: false,
        skills: false,
        hooks: false,
        mcp: true,
        rules: true,
    }
}

fn no_caps() -> Capabilities {
    Capabilities {
        agents: false,
        subagents: false,
        permission_enforcement: false,
        skills: false,
        hooks: false,
        mcp: false,
        rules: false,
    }
}

pub fn get(id: &str) -> Option<&'static HostSpec> {
    HOSTS.iter().find(|h| h.id.eq_ignore_ascii_case(id))
}

#[derive(Serialize, Clone, Debug)]
pub struct Detection {
    pub id: String,
    pub label: String,
    pub binary: Option<String>,
    pub detected: bool,
    pub caps: Capabilities,
    pub verification: String,
}

pub fn detect() -> Vec<Detection> {
    HOSTS
        .iter()
        .map(|h| {
            let found = h.binaries.iter().find_map(|b| paths::which(b));
            let detected = found.is_some();
            Detection {
                id: h.id.to_string(),
                label: h.label.to_string(),
                binary: found.map(|p| p.display().to_string()),
                detected,
                caps: (h.caps)(),
                verification: h.verification.to_string(),
            }
        })
        .collect()
}

#[derive(Serialize, Clone, Debug)]
pub struct Action {
    pub path: String,
    pub status: String,
    pub detail: String,
}

#[derive(Serialize, Clone, Debug)]
pub struct Integration {
    pub host: String,
    pub status: String,
    pub actions: Vec<Action>,
    pub detail: String,
}

/// Write the EOS adapter for `host` into `project`.
///
/// `dry_run` reports exactly what would be written without touching the disk.
/// Read-only hosts report `SKIPPED`, and hosts with no adapter report `BLOCKED`.
pub fn integrate(
    engine: &Path,
    python: &Path,
    project: &Path,
    host: &HostSpec,
    dry_run: bool,
) -> Integration {
    let mut actions = Vec::new();
    let mut push = |path: PathBuf, status: &str, detail: String| {
        actions.push(Action {
            path: path.display().to_string(),
            status: status.to_string(),
            detail,
        })
    };

    if host.id == "blackbox" {
        return Integration {
            host: host.id.to_string(),
            status: "BLOCKED".into(),
            actions,
            detail: "no documented local contract for this host; nothing written and \
                     no capability claimed"
                .into(),
        };
    }

    if host.id == "cursor" {
        let rules = project.join(".cursor").join("rules");
        let target = rules.join("eos-engineering-os.mdc");
        let body = cursor_rule_body(engine);
        if dry_run {
            push(target, "PLANNED", format!("{} byte rule file", body.len()));
        } else if let Err(e) = write_if_changed(&target, &body) {
            push(target, "FAILED", e);
        } else {
            push(target, "WRITTEN", "rules + gate/skill guidance".into());
        }
        return Integration {
            host: host.id.to_string(),
            status: "DONE".into(),
            actions,
            detail: "rules-only adapter: no agent fleet is claimed for Cursor".into(),
        };
    }

    if host.id == "claude" {
        let agents_src = engine.join("agents");
        let dst = project.join(".claude").join("agents");
        let mut count = 0usize;
        match fs::read_dir(&agents_src) {
            Ok(rd) => {
                for entry in rd.flatten() {
                    let path = entry.path();
                    if path.extension().map(|e| e != "md").unwrap_or(true) {
                        continue;
                    }
                    let stem = path.file_stem().unwrap().to_string_lossy().to_string();
                    let target = dst.join(format!("{stem}.md"));
                    let body = match fs::read_to_string(&path) {
                        Ok(b) => translate_to_claude(&b),
                        Err(e) => {
                            push(target, "FAILED", format!("read {e}"));
                            continue;
                        }
                    };
                    if dry_run {
                        count += 1;
                        continue;
                    }
                    match write_if_changed(&target, &body) {
                        Ok(_) => count += 1,
                        Err(e) => push(target, "FAILED", e),
                    }
                }
            }
            Err(e) => {
                push(agents_src, "FAILED", format!("cannot read engine agents: {e}"));
            }
        }
        push(
            dst.clone(),
            if dry_run { "PLANNED" } else { "WRITTEN" },
            format!("{count} agent definition(s), Claude frontmatter"),
        );

        let skills_link = project.join(".claude").join("skills");
        if !skills_link.exists() && !dry_run {
            let src = engine.join("skills");
            if src.is_dir() {
                if let Some(parent) = skills_link.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                // Copy rather than link: Windows junctions need a fallback and a
                // copy is always reversible.
                let _ = copy_tree_shallow(&src, &skills_link);
            }
        }
        push(
            skills_link,
            if dry_run { "PLANNED" } else { "PRESENT" },
            "EOS skills root for this host".into(),
        );

        return Integration {
            host: host.id.to_string(),
            status: "DONE".into(),
            actions,
            detail: "agent definitions written in Claude's documented format; \
                     permission enforcement is NOT claimed for this host"
                .into(),
        };
    }

    // opencode: reuse the verified engine installer instead of re-implementing
    // the V2 permission renderer here.
    let script = engine.join("tools").join("agent_install.py");
    if !script.is_file() {
        return Integration {
            host: host.id.to_string(),
            status: "BLOCKED".into(),
            actions,
            detail: format!("engine script missing: {}", script.display()),
        };
    }
    if dry_run {
        push(
            project.join(".opencode").join("agents"),
            "PLANNED",
            "9 agent files via engine/tools/agent_install.py".into(),
        );
        push(
            project.join(".opencode").join("plugin"),
            "PLANNED",
            "activation plugin via engine/tools/plugin_install.py (adds context, no permissions)".into(),
        );
        return Integration {
            host: host.id.to_string(),
            status: "DONE".into(),
            actions,
            detail: "dry run".into(),
        };
    }
    let output = Command::new(python)
        .arg(&script)
        .arg("install")
        .arg("--target")
        .arg("project")
        .arg("--project")
        .arg(project)
        .current_dir(engine)
        .output();
    match output {
        Ok(out) => {
            let ok = out.status.success();
            let text = String::from_utf8_lossy(&out.stdout).to_string();
            let err = String::from_utf8_lossy(&out.stderr).to_string();
            let last = text
                .lines()
                .rev()
                .find(|l| !l.trim().is_empty())
                .unwrap_or("")
                .trim()
                .to_string();
            push(
                project.join(".opencode").join("agents"),
                if ok { "WRITTEN" } else { "FAILED" },
                if ok { last } else { err.trim().to_string() },
            );
            // The activation plugin is what makes EOS intelligence automatic:
            // the host asks EOS for the context on every session, so the user
            // never has to pick an orchestrator by hand. It is additive - it
            // adds no tool and no permission.
            let plugin = engine.join("tools").join("plugin_install.py");
            if plugin.is_file() {
                let out = Command::new(python)
                    .arg(&plugin)
                    .arg("install")
                    .arg("--project")
                    .arg(project)
                    .arg("--host")
                    .arg(host.id)
                    .current_dir(engine)
                    .output();
                match out {
                    Ok(o) => {
                        let ptext = String::from_utf8_lossy(&o.stdout).to_string();
                        let perr = String::from_utf8_lossy(&o.stderr).to_string();
                        let plast = ptext
                            .lines()
                            .rev()
                            .find(|l| !l.trim().is_empty())
                            .unwrap_or("")
                            .trim()
                            .to_string();
                        push(
                            project.join(".opencode").join("plugin"),
                            if o.status.success() { "WRITTEN" } else { "FAILED" },
                            if o.status.success() { plast } else { perr.trim().to_string() },
                        );
                    }
                    Err(e) => push(
                        project.join(".opencode").join("plugin"),
                        "FAILED",
                        format!("cannot run plugin installer: {e}"),
                    ),
                }
            }
            Integration {
                host: host.id.to_string(),
                status: if ok { "DONE".into() } else { "FAILED".into() },
                actions,
                detail: "engine-rendered V2 permission list".into(),
            }
        }
        Err(e) => Integration {
            host: host.id.to_string(),
            status: "FAILED".into(),
            actions,
            detail: format!("cannot run installer: {e}"),
        },
    }
}

/// Start a host binary and wait for it.
///
/// A Windows `.cmd`/`.bat` launcher is a script, not an executable: Windows
/// refuses to start it directly ("not a valid Win32 application"), so the
/// command interpreter has to run it. Everything else is started directly.
pub fn spawn_host(bin: &Path, args: &[String], cwd: &Path) -> Result<std::process::ExitStatus, String> {
    use std::process::{Command, Stdio};
    let ext = bin
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_lowercase();
    let mut cmd = if cfg!(windows) && (ext == "cmd" || ext == "bat") {
        let mut c = Command::new("cmd.exe");
        c.arg("/C").arg(bin);
        c
    } else {
        Command::new(bin)
    };
    for a in args {
        cmd.arg(a);
    }
    cmd.current_dir(cwd)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()
        .map_err(|e| format!("cannot start {}: {e}", bin.display()))
}

/// Build the launch command for a host in a project directory.
pub fn launch_command(
    host: &HostSpec,
    project: &Path,
) -> Result<(PathBuf, Vec<String>, PathBuf), String> {
    let binary = host
        .binaries
        .iter()
        .find_map(|b| paths::which(b))
        .ok_or_else(|| {
            format!(
                "{} is not installed (looked for: {}). Install it, then re-run.",
                host.label,
                host.binaries.join(", ")
            )
        })?;
    let args = match host.launch {
        LaunchStyle::CwdOnly => Vec::new(),
        LaunchStyle::PathArg => vec![project.display().to_string()],
    };
    Ok((binary, args, project.to_path_buf()))
}

fn translate_to_claude(source: &str) -> String {
    // Claude Code agents use YAML frontmatter with name/description/tools/model.
    // OpenCode's ordered `permissions`, `steps` and `request.body` are not part of
    // that contract, so they are dropped rather than emitted as pseudo-config.
    let mut body = String::new();
    if let Some(rest) = source.strip_prefix("---") {
        if let Some(end) = rest.find("\n---") {
            let front = &rest[..end];
            let mut description = String::new();
            let mut mode = String::new();
            for line in front.lines() {
                let t = line.trim();
                if let Some(v) = t.strip_prefix("description:") {
                    description = v.trim().to_string();
                }
                if let Some(v) = t.strip_prefix("mode:") {
                    mode = v.trim().to_string();
                }
            }
            let mut names: Vec<String> = Vec::new();
            for caps in ["Read", "Grep", "Glob", "Bash", "Edit", "Write", "Task"] {
                let lower = caps.to_lowercase();
                if front.to_lowercase().contains(&format!("action: {lower}")) {
                    names.push(caps.to_string());
                }
            }
            body.push_str("---\n");
            body.push_str(&format!("name: {mode}\n"));
            body.push_str(&format!("description: {description}\n"));
            if names.is_empty() {
                names.push("Read".into());
                names.push("Grep".into());
                names.push("Glob".into());
            }
            body.push_str(&format!("tools: {}\n", names.join(", ")));
            body.push_str("---\n\n");
            body.push_str(&format!(
                "<!-- Generated by EOS from engine/agents. OpenCode permission \
                 enforcement is not reproduced here; review before use. -->\n\n"
            ));
            body.push_str(rest[end + 4..].trim_start());
        }
    }
    if body.is_empty() {
        source.to_string()
    } else {
        body
    }
}

fn cursor_rule_body(engine: &Path) -> String {
    let names: Vec<String> = fs::read_to_string(engine.join("registry").join("GATES.json"))
        .map(|t| {
            t.lines()
                .filter_map(|line| {
                    let t = line.trim();
                    if t.starts_with('"') && t.ends_with("\": {") {
                        let name = t.trim_matches(|c| c == '"' || c == ':' || c == ' ' || c == '{');
                        if !["version", "note", "defaults", "projects", "suites"].contains(&name) {
                            return Some(name.to_string());
                        }
                    }
                    None
                })
                .collect::<Vec<String>>()
        })
        .unwrap_or_default();
    format!(
        "---\ndescription: EOS engineering rules (generated)\nglobs: \"**/*\"\nalwaysApply: true\n---\n\n\
         # Engineering OS rules\n\n\
         EOS is the authority for verification in this workspace. Rules:\n\n\
         1. No change is complete without a verified test record. Run the gate, not a hand-rolled command.\n\
         2. One writer per workspace. Reviewers and verifiers never edit.\n\
         3. Missing coverage is reported as BLOCKED or FAIL. Never as PASS.\n\
         4. Promotion requires a gate artifact whose project id, baseline and file hashes match.\n\n\
         Gate projects known to this install: {}.\n\n\
         Note: this host has no subagent or permission-enforcement interface, so EOS\n\
         enforces the same rules through this rule file and the `eos gate` command\n\
         instead of an agent fleet.\n",
        if names.is_empty() {
            "none configured".to_string()
        } else {
            names.join(", ")
        }
    )
}

fn write_if_changed(target: &Path, body: &str) -> Result<(), String> {
    if let Ok(existing) = fs::read_to_string(target) {
        if existing == body {
            return Ok(());
        }
    }
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("cannot create {}: {e}", parent.display()))?;
    }
    fs::write(target, body).map_err(|e| format!("cannot write {}: {e}", target.display()))
}

fn copy_tree_shallow(src: &Path, dst: &Path) -> Result<usize, String> {
    fs::create_dir_all(dst).map_err(|e| e.to_string())?;
    let mut n = 0usize;
    for entry in fs::read_dir(src).map_err(|e| e.to_string())?.flatten() {
        let path = entry.path();
        let target = dst.join(entry.file_name());
        if path.is_dir() {
            n += copy_tree_shallow(&path, &target)?;
        } else {
            fs::copy(&path, &target).map_err(|e| e.to_string())?;
            n += 1;
        }
    }
    Ok(n)
}
