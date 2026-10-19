//! EOS command line.
//!
//! Deliberately thin: every decision that matters (what a gate runs, whether a
//! promotion is valid, which skill wins) lives in the Python engine that is
//! covered by self-tests. The CLI resolves the workspace, finds the engine and
//! forwards the call with the exit code intact.
//!
//! No external crates: it must build offline on a fresh machine.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

const VERSION: &str = "0.1.0-alpha";

struct Engine {
    root: PathBuf,
    tools: PathBuf,
    router: PathBuf,
}

impl Engine {
    fn script(&self, name: &str, prefer_router: bool) -> PathBuf {
        let tools_hit = self.tools.join(name);
        let router_hit = self.router.join(name);
        if prefer_router && router_hit.is_file() {
            router_hit
        } else if tools_hit.is_file() {
            tools_hit
        } else {
            router_hit
        }
    }
}

fn find_engine() -> Result<Engine, String> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(root) = env::var("EOS_ROOT") {
        candidates.push(PathBuf::from(root));
    }
    if let Ok(cwd) = env::current_dir() {
        candidates.extend(cwd.ancestors().map(|p| p.to_path_buf()));
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.extend(dir.ancestors().map(|p| p.to_path_buf()));
        }
    }

    // Pass 1: a live control plane wins. Pass 2: a standalone engine/ tree, so an
    // installed copy works without a workspace.
    for (tools_rel, router_rel) in [(".agents/tools", ".agents/router"), ("engine/tools", "engine/router")] {
        for root in &candidates {
            let tools = root.join(tools_rel);
            let router = root.join(router_rel);
            if tools.join("quality_gate.py").is_file() {
                return Ok(Engine { root: root.clone(), tools, router });
            }
        }
    }
    Err("cannot locate the EOS engine (looked for .agents/tools or engine/tools). \
         Set EOS_ROOT to the workspace root."
        .to_string())
}

fn python() -> String {
    env::var("EOS_PYTHON").unwrap_or_else(|_| "python".to_string())
}

fn run_script(engine: &Engine, name: &str, prefer_router: bool, args: &[String]) -> i32 {
    let path = engine.script(name, prefer_router);
    if !path.is_file() {
        eprintln!("BLOCKED: engine script not found: {}", path.display());
        return 2;
    }
    match Command::new(python())
        .arg(&path)
        .args(args)
        .current_dir(&engine.root)
        .status()
    {
        Ok(status) => status.code().unwrap_or(1),
        Err(err) => {
            eprintln!("BLOCKED: cannot start {}: {err}", python());
            2
        }
    }
}

fn capture(cmd: &mut Command) -> (i32, String, String) {
    match cmd.output() {
        Ok(out) => (
            out.status.code().unwrap_or(1),
            String::from_utf8_lossy(&out.stdout).to_string(),
            String::from_utf8_lossy(&out.stderr).to_string(),
        ),
        Err(err) => (2, String::new(), err.to_string()),
    }
}

fn usage() {
    println!(
        "eos {VERSION} — Engineering OS

usage: eos <command>

  skills discover                     index every discoverable skill
  skills resolve  --task T [--project P] [-k N]
  skills explain  --task T [--project P] [-k N]
  skills doctor                       structural and collision findings
  gate run        --project P --suite S [--timeout N]
  gate verify     --file F [--project P] [--max-age-min N]
  gate list
  gate show       --file F
  promote         --project P --gate G --artifact A [--label L]
  promote verify  --file F
  graph report    --project P [--json]
  graph fallback  --project P
  graph invalidate --project P [--confirm]
  scan            [paths...] [--json]
  publish sync | publish verify | publish status
  agents install  [--target project|global] [--force]
  agents status
  agents verify                       runtime registration + permission proof
  route           --task T
  snapshot create | verify | restore-test
  doctor                              end-to-end readiness check
  version

Environment: EOS_ROOT (workspace root), EOS_PYTHON (interpreter)."
    );
}

fn doctor(engine: &Engine) -> i32 {
    println!("EOS doctor — {}", engine.root.display());
    let mut failures = 0usize;
    let mut warnings = 0usize;

    let (code, out, _) = capture(Command::new(python()).arg("-c").arg("import sys; print(sys.version.split()[0])"));
    if code == 0 {
        println!("  PASS python {}", out.trim());
    } else {
        println!("  FAIL python unavailable (set EOS_PYTHON)");
        failures += 1;
    }

    for name in ["quality_gate.py", "promote.py", "skill_index.py", "graph_freshness.py"] {
        let present = engine.tools.join(name).is_file();
        println!("  {} {}", if present { "PASS" } else { "FAIL" }, name);
        if !present {
            failures += 1;
        }
    }

    let backups = engine.root.join(".agents").join("backups");
    let has_baseline = fs::read_dir(&backups)
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .any(|e| e.file_name().to_string_lossy().starts_with("baseline_"))
        })
        .unwrap_or(false);
    if has_baseline {
        println!("  PASS baseline snapshot present");
    } else {
        println!("  FAIL no baseline snapshot (gates refuse to run without one)");
        failures += 1;
    }

    let agents_test = engine.tools.join("agent_dispatch_test.py");
    if agents_test.is_file() {
        let (code, out, err) = capture(Command::new(python())
            .arg(&agents_test)
            .current_dir(&engine.root));
        let last = out.lines().last().unwrap_or("").trim().to_string();
        if code == 0 {
            println!("  PASS agents: {last}");
        } else {
            println!("  FAIL agents: {}", if last.is_empty() { err.trim() } else { &last });
            failures += 1;
        }
    }

    let index = engine.root.join(".agents").join("registry").join("SKILL_INDEX.json");
    if index.is_file() {
        println!("  PASS skill index present");
    } else {
        println!("  WARN skill index missing (run: eos skills discover)");
        warnings += 1;
    }

    println!(
        "\n{}: {} failure(s), {} warning(s)",
        if failures == 0 { "READY" } else { "NOT READY" },
        failures,
        warnings
    );
    if failures == 0 { 0 } else { 1 }
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.is_empty() || args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
        usage();
        return ExitCode::SUCCESS;
    }
    if args[0] == "version" || args[0] == "--version" || args[0] == "-v" {
        println!("eos {VERSION}");
        return ExitCode::SUCCESS;
    }

    let engine = match find_engine() {
        Ok(e) => e,
        Err(msg) => {
            eprintln!("BLOCKED: {msg}");
            return ExitCode::from(2);
        }
    };

    let rest: Vec<String> = args[1..].to_vec();
    let code = match args[0].as_str() {
        "skills" => match rest.first().map(String::as_str) {
            Some("discover") => run_script(&engine, "skill_index.py", false, &[vec!["discover".into()], rest[1..].to_vec()].concat()),
            Some("doctor") => run_script(&engine, "skill_index.py", false, &[vec!["doctor".into()], rest[1..].to_vec()].concat()),
            Some("resolve") => run_script(&engine, "skill_index.py", false, &[vec!["resolve".into()], rest[1..].to_vec()].concat()),
            Some("explain") => run_script(&engine, "skill_index.py", false, &[vec!["explain".into()], rest[1..].to_vec()].concat()),
            _ => {
                eprintln!("usage: eos skills discover|resolve|explain|doctor");
                2
            }
        },
        "gate" => match rest.first().map(String::as_str) {
            Some(cmd @ ("run" | "verify" | "list" | "show")) => {
                run_script(&engine, "quality_gate.py", false, &[vec![cmd.to_string()], rest[1..].to_vec()].concat())
            }
            _ => {
                eprintln!("usage: eos gate run|verify|list|show");
                2
            }
        },
        "promote" => match rest.first().map(String::as_str) {
            Some("verify") => run_script(&engine, "promote.py", false, &[vec!["verify".into()], rest[1..].to_vec()].concat()),
            _ => run_script(&engine, "promote.py", false, &[vec!["promote".into()], rest.to_vec()].concat()),
        },
        "graph" => match rest.first().map(String::as_str) {
            Some(cmd @ ("report" | "fallback" | "invalidate")) => {
                run_script(&engine, "graph_freshness.py", false, &[vec![cmd.to_string()], rest[1..].to_vec()].concat())
            }
            _ => {
                eprintln!("usage: eos graph report|fallback|invalidate");
                2
            }
        },
        "scan" => run_script(&engine, "secret_scan.py", false, &rest),
        "publish" => match rest.first().map(String::as_str) {
            Some(cmd @ ("sync" | "verify" | "status")) => {
                run_script(&engine, "publish.py", false, &[vec![cmd.to_string()], rest[1..].to_vec()].concat())
            }
            _ => {
                eprintln!("usage: eos publish sync|verify|status");
                2
            }
        },
        "agents" => match rest.first().map(String::as_str) {
            Some("install") => run_script(&engine, "agent_install.py", false, &[vec!["install".into()], rest[1..].to_vec()].concat()),
            Some("status") => run_script(&engine, "agent_install.py", false, &[vec!["status".into()], rest[1..].to_vec()].concat()),
            Some("verify") => run_script(&engine, "agent_dispatch_test.py", false, &rest[1..]),
            _ => {
                eprintln!("usage: eos agents install|status|verify");
                2
            }
        },
        "route" => run_script(&engine, "route_task.py", true, &rest),
        "snapshot" => match rest.first().map(String::as_str) {
            Some(cmd @ ("create" | "verify" | "restore-test" | "restore")) => {
                run_script(&engine, "snapshot.py", false, &[vec![cmd.to_string()], rest[1..].to_vec()].concat())
            }
            _ => {
                eprintln!("usage: eos snapshot create|verify|restore-test|restore");
                2
            }
        },
        "doctor" => doctor(&engine),
        other => {
            eprintln!("unknown command: {other}");
            usage();
            2
        }
    };

    ExitCode::from(u8::try_from(code.clamp(0, 255)).unwrap_or(1))
}

#[allow(dead_code)]
fn exists(path: &Path) -> bool {
    path.exists()
}
