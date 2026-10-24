//! EOS command line.
//!
//! Two kinds of command live here:
//!
//! * **Product commands** (open, workspace, hosts, install, uninstall, repair,
//!   update, rollback, doctor) are implemented in Rust because they are about
//!   *this machine*: which directories exist, which host is installed, which
//!   runtime to use, what PATH says.
//! * **Engineering commands** (skills, gate, promote, graph, scan, publish,
//!   agents, route, snapshot) are forwarded to the Python engine, which is the
//!   single authority for engineering decisions and is covered by self-tests.
//!
//! The CLI never decides whether work passed. It only locates things and
//! reports what happened, with the engine's exit code intact.

mod hosts;
mod install;
mod paths;
mod workspace;

use serde_json::json;
use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};

const VERSION: &str = install::VERSION;

/// Print to standard output without ever panicking.
///
/// A GUI installer (NSIS) runs this binary with no console attached, and a
/// broken pipe is normal when another program reads our output. `println!`
/// panics in both cases, which would turn a *successful* install into a crash
/// in the installer's eyes. A product that other programs run has to report,
/// not die.
macro_rules! say {
    () => {{ let _ = writeln!(&mut std::io::stdout(),); }};
    ($($arg:tt)*) => {{ let _ = writeln!(&mut std::io::stdout(), $($arg)*); }};
}

/// The same contract for diagnostics on standard error.
macro_rules! warn {
    () => {{ let _ = writeln!(&mut std::io::stderr(),); }};
    ($($arg:tt)*) => {{ let _ = writeln!(&mut std::io::stderr(), $($arg)*); }};
}

struct Engine {
    root: PathBuf,
    tools: PathBuf,
    router: PathBuf,
    python: PathBuf,
    python_source: String,
}

fn load_engine() -> Result<Engine, String> {
    let root = paths::resolve_engine()?;
    let (python, python_source) = paths::resolve_python(&root)?;
    Ok(Engine {
        tools: root.join("tools"),
        router: root.join("router"),
        root,
        python,
        python_source,
    })
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

/// The project tree an operation is about.
///
/// Order: an explicit selector, then the nearest ancestor carrying EOS project
/// identity, then the nearest ancestor that is a workspace control plane, then
/// the working directory. The engine's own location is never used: an installed
/// EOS must be able to operate on somebody else's project.
fn resolve_workspace(explicit: Option<&Path>) -> PathBuf {
    if let Some(p) = explicit {
        let p = fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
        return paths::clean(&p);
    }
    if let Some(v) = env::var_os("EOS_WORKSPACE") {
        let p = PathBuf::from(v);
        if p.is_dir() {
            let p = fs::canonicalize(&p).unwrap_or(p);
            return paths::clean(&p);
        }
    }
    let cwd = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let cwd = paths::clean(&fs::canonicalize(&cwd).unwrap_or(cwd));
    for ancestor in cwd.ancestors() {
        if workspace::project_config_path(ancestor).is_file() {
            return ancestor.to_path_buf();
        }
    }
    for ancestor in cwd.ancestors() {
        if paths::is_engine(&ancestor.join(".agents")) {
            return ancestor.to_path_buf();
        }
    }
    cwd
}

fn run_script(engine: &Engine, name: &str, prefer_router: bool, args: &[String], ws: &Path) -> i32 {
    let path = engine.script(name, prefer_router);
    if !path.is_file() {
        warn!("BLOCKED: engine script not found: {}", path.display());
        return 2;
    }
    match Command::new(&engine.python)
        .arg(&path)
        .args(args)
        .current_dir(ws)
        .env("EOS_WORKSPACE", ws)
        .env("EOS_HOME", paths::eos_home())
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .status()
    {
        Ok(status) => status.code().unwrap_or(1),
        Err(err) => {
            warn!("BLOCKED: cannot start {}: {err}", engine.python.display());
            2
        }
    }
}

fn capture(cmd: &mut Command) -> (i32, String, String) {
    match cmd.output() {
        Ok(out) => {
            let code = if out.status.success() {
                0
            } else if out.status.code().is_none() {
                2
            } else {
                out.status.code().unwrap_or(1)
            };
            (
                code,
                String::from_utf8_lossy(&out.stdout).to_string(),
                String::from_utf8_lossy(&out.stderr).to_string(),
            )
        }
        Err(err) => (2, String::new(), err.to_string()),
    }
}

// ------------------------------------------------------------- argument tools

struct Args {
    positionals: Vec<String>,
    flags: Vec<(String, Option<String>)>,
}

impl Args {
    fn parse(raw: &[String]) -> Args {
        let mut positionals = Vec::new();
        let mut flags: Vec<(String, Option<String>)> = Vec::new();
        let mut i = 0;
        while i < raw.len() {
            let arg = &raw[i];
            if let Some(rest) = arg.strip_prefix("--") {
                let (name, value) = match rest.split_once('=') {
                    Some((n, v)) => (n.to_string(), Some(v.to_string())),
                    None => (rest.to_string(), None),
                };
                if value.is_none()
                    && i + 1 < raw.len()
                    && !raw[i + 1].starts_with("--")
                {
                    flags.push((name, Some(raw[i + 1].clone())));
                    i += 2;
                    continue;
                }
                flags.push((name, value));
                i += 1;
            } else {
                positionals.push(arg.clone());
                i += 1;
            }
        }
        Args { positionals, flags }
    }

    fn has(&self, name: &str) -> bool {
        self.flags.iter().any(|(n, _)| n == name)
    }

    fn value(&self, name: &str) -> Option<String> {
        self.flags
            .iter()
            .find(|(n, _)| n == name)
            .and_then(|(_, v)| v.clone())
    }

    fn json(&self) -> bool {
        self.has("json")
    }
}

fn usage() {
    say!(
        "eos {VERSION} — Engineering OS

usage: eos <command> [options]

Product
  open <path> [--host opencode|claude|cursor|blackbox] [--no-integrate] [--print] [--json]
                                       integrate a project and start a host there
  workspace add <path> [--name N] | list [--json] | remove <id> | relocate <id> <path>
                                       project identity and relocation-safe registry
  hosts detect | doctor [--json]       which AI hosts exist and what they can really do
  install [--local <dir>] [--source D] [--version V] [--force] [--add-to-path] [--json]
                                       install program files; --local syncs a workspace
  uninstall [--purge-config] [--keep-versions]
  repair  [--source D] [--version V]
  update  [--source D] [--version V] [--force]
  rollback
  doctor [--product] [--json]          core readiness, or product readiness
  version

Engineering (forwarded to the Python engine)
  skills discover | resolve | explain | doctor | verify <name> [--check C] | provenance <name>
  gate run --project P --suite S | gate verify --file F | gate list | gate show --file F
  promote ... | promote verify --file F
  graph report | fallback | invalidate
  scan [paths...] [--json]
  publish sync | verify | status
  agents install | status | uninstall | list [--json] | explain <name> [--prompt] | test | permissions [--json]
  orchestrators discover | doctor | test [--self-test] [--provider <id>] | run --provider <id> --plan <file> [--workspace DIR]
  route --task T
  snapshot create | verify | restore-test
  guardian scan | map | audit | verify | status

Environment: EOS_HOME (user data, default ~/.eos), EOS_WORKSPACE (project in scope),
EOS_ENGINE (engine directory), EOS_PYTHON (interpreter)."
    );
}

// ------------------------------------------------------------------ doctor

fn doctor_core(engine: &Engine, ws: &Path, as_json: bool) -> i32 {
    let mut checks: Vec<(String, String, String)> = Vec::new();
    let mut failures = 0usize;
    let mut warnings = 0usize;

    let (code, out, err) = capture(Command::new(&engine.python).arg("-c").arg(
        "import sys; print(sys.version.split()[0])",
    ));
    let python_detail = format!("{} ({})", out.trim(), engine.python_source);
    checks.push((
        if code == 0 { "PASS" } else { "FAIL" }.into(),
        "python".into(),
        if code == 0 { python_detail } else { err.trim().to_string() },
    ));
    if code != 0 {
        failures += 1;
    }

    for name in [
        "quality_gate.py",
        "promote.py",
        "skill_index.py",
        "graph_freshness.py",
        "agent_install.py",
        "_eos_paths.py",
    ] {
        let present = engine.tools.join(name).is_file();
        checks.push((
            if present { "PASS" } else { "FAIL" }.into(),
            format!("engine/{name}"),
            if present { "present".into() } else { "missing".into() },
        ));
        if !present {
            failures += 1;
        }
    }

    let baselines = engine.root.join("backups");
    let home_baselines = paths::eos_home().join("baselines");
    let has_baseline = [&baselines, &home_baselines].iter().any(|d| {
        fs::read_dir(d)
            .map(|rd| {
                rd.filter_map(|e| e.ok())
                    .any(|e| e.file_name().to_string_lossy().starts_with("baseline_"))
            })
            .unwrap_or(false)
    });
    checks.push((
        if has_baseline { "PASS" } else { "WARN" }.into(),
        "baseline".into(),
        if has_baseline {
            "snapshot present".into()
        } else {
            format!(
                "none in {} or {} — gates refuse to run without one",
                baselines.display(),
                home_baselines.display()
            )
        },
    ));
    if !has_baseline {
        warnings += 1;
    }

    let index = engine.root.join("registry").join("SKILL_INDEX.json");
    checks.push((
        if index.is_file() { "PASS" } else { "WARN" }.into(),
        "skill index".into(),
        index.display().to_string(),
    ));
    if !index.is_file() {
        warnings += 1;
    }

    let gates = engine.root.join("registry").join("GATES.json");
    let mut configured = 0usize;
    let mut declared = 0usize;
    if let Ok(text) = fs::read_to_string(&gates) {
        configured = text.matches("\"cmd\":").count();
        declared = text.matches("NOT_CONFIGURED").count();
    }
    checks.push((
        if configured > 0 { "PASS" } else { "FAIL" }.into(),
        "gate commands".into(),
        format!("{configured} executable command(s), {declared} suite(s) NOT_CONFIGURED"),
    ));
    if configured == 0 {
        failures += 1;
    }

    checks.push((
        "PASS".into(),
        "workspace".into(),
        ws.display().to_string(),
    ));

    if as_json {
        let verdict = if failures == 0 { "READY" } else { "NOT_READY" };
        let body = json!({
            "mode": "core",
            "verdict": verdict,
            "engine_root": engine.root.display().to_string(),
            "eos_home": paths::eos_home().display().to_string(),
            "workspace": ws.display().to_string(),
            "failures": failures,
            "warnings": warnings,
            "checks": checks.iter().map(|(s, n, d)| json!({"status": s, "check": n, "detail": d})).collect::<Vec<_>>(),
        });
        say!("{}", serde_json::to_string_pretty(&body).unwrap());
    } else {
        say!("EOS doctor (core) — engine {}", engine.root.display());
        for (status, name, detail) in &checks {
            say!("  {status:<4} {name:<20} {detail}");
        }
        say!(
            "\n{}: {} failure(s), {} warning(s)\nThis verdict covers the engineering core only. \
             Run `eos doctor --product` for install readiness.",
            if failures == 0 { "READY" } else { "NOT READY" },
            failures,
            warnings
        );
    }
    if failures == 0 {
        0
    } else {
        1
    }
}

fn doctor_product(engine: &Engine, ws: &Path, as_json: bool) -> i32 {
    let mut checks: Vec<(String, String, String)> = Vec::new();
    let mut failures = 0usize;
    let cfg = paths::load_config();

    // 1. installed and verified
    match cfg.active_version.clone() {
        Some(version) => match install::verify_install(&version) {
            Ok((ok, missing, changed)) => {
                let bad = missing.len() + changed.len();
                checks.push((
                    if bad == 0 { "PASS" } else { "FAIL" }.into(),
                    "installed version".into(),
                    format!("{version}: {ok} file(s) verified, {bad} problem(s)"),
                ));
                if bad > 0 {
                    failures += 1;
                }
            }
            Err(e) => {
                checks.push(("FAIL".into(), "installed version".into(), e));
                failures += 1;
            }
        },
        None => {
            checks.push((
                "FAIL".into(),
                "installed version".into(),
                "no version installed (run: eos install)".into(),
            ));
            failures += 1;
        }
    }

    // 2. runtime independent of the machine's Python
    let bundled = engine.python_source == "bundled";
    checks.push((
        if bundled { "PASS" } else { "FAIL" }.into(),
        "standalone runtime".into(),
        if bundled {
            format!("bundled interpreter: {}", engine.python.display())
        } else {
            format!(
                "using the machine's interpreter ({}) — a clean machine without Python cannot run this \
                 install; ship a packaged runtime",
                engine.python.display()
            )
        },
    ));
    if !bundled {
        failures += 1;
    }

    // 3. launcher on PATH
    let bin = paths::bin_dir();
    let on_path = env::var_os("PATH")
        .map(|p| {
            env::split_paths(&p).any(|d| {
                d.to_string_lossy()
                    .trim_end_matches('\\')
                    .eq_ignore_ascii_case(bin.to_string_lossy().trim_end_matches('\\'))
            })
        })
        .unwrap_or(false)
        || install::user_path_contains(&bin);
    checks.push((
        if on_path { "PASS" } else { "FAIL" }.into(),
        "launcher on PATH".into(),
        format!(
            "{} {}",
            bin.display(),
            if on_path {
                "is on PATH (persisted user PATH; a new shell picks it up)"
            } else {
                "is NOT on PATH"
            }
        ),
    ));
    if !on_path {
        failures += 1;
    }

    // 4. a real host, with a real adapter
    let detected = hosts::detect();
    let usable = detected.iter().find(|d| d.detected && d.caps.agents);
    match usable {
        Some(host) => checks.push((
            "PASS".into(),
            "host adapter".into(),
            format!("{} at {}", host.label, host.binary.clone().unwrap_or_default()),
        )),
        None => {
            checks.push((
                "FAIL".into(),
                "host adapter".into(),
                "no installed host declares agent support; the product cannot hand work to a runtime"
                    .into(),
            ));
            failures += 1;
        }
    }

    // 5. project identity exists somewhere
    let registry = workspace::load_registry();
    let with_identity = registry
        .workspaces
        .iter()
        .filter(|w| workspace::project_config_path(Path::new(&w.path)).is_file())
        .count();
    checks.push((
        if with_identity > 0 { "PASS" } else { "FAIL" }.into(),
        "project identity".into(),
        format!("{with_identity} registered project(s) with .eos/project.json"),
    ));
    if with_identity == 0 {
        failures += 1;
    }

    // 6. at least one real gate suite
    let gates = engine.root.join("registry").join("GATES.json");
    let mut configured = 0usize;
    if let Ok(text) = fs::read_to_string(&gates) {
        configured = text.matches("\"cmd\":").count();
    }
    checks.push((
        if configured > 0 { "PASS" } else { "FAIL" }.into(),
        "real gate suite".into(),
        format!("{configured} executable gate command(s) in GATES.json"),
    ));
    if configured == 0 {
        failures += 1;
    }

    // 7. the host integration is complete: the agent contracts and the
    //    activation plugin that makes EOS automatic in a native session.
    let dispatch = engine.root.join("tools").join("agent_dispatch_test.py");
    let plugin = engine.root.join("tools").join("plugin_install.py");
    let plugin_file = ws.join(".opencode").join("plugin").join("eos-activation.js");
    if dispatch.is_file() {
        let out = Command::new(&engine.python)
            .arg(&dispatch)
            .current_dir(ws)
            .env("EOS_WORKSPACE", ws)
            .env("EOS_HOME", paths::eos_home())
            .output();
        match out {
            Ok(o) if o.status.success() => {
                let last = String::from_utf8_lossy(&o.stdout)
                    .lines()
                    .rev()
                    .find(|l| !l.trim().is_empty())
                    .unwrap_or("")
                    .trim()
                    .to_string();
                checks.push(("PASS".into(), "agent contracts".into(), last));
            }
            Ok(o) => {
                let last = String::from_utf8_lossy(&o.stdout)
                    .lines()
                    .rev()
                    .find(|l| !l.trim().is_empty())
                    .unwrap_or("the host did not accept the EOS agent contracts")
                    .trim()
                    .to_string();
                checks.push(("FAIL".into(), "agent contracts".into(), last));
                failures += 1;
            }
            Err(e) => {
                checks.push(("WARN".into(), "agent contracts".into(), format!("cannot verify: {e}")));
            }
        }
    }
    if plugin.is_file() {
        let current = plugin_file.is_file()
            && fs::read_to_string(&plugin_file)
                .map(|a| fs::read_to_string(engine.root.join("host-plugins").join("opencode").join("eos-activation.js")).map(|b| a == b).unwrap_or(false))
                .unwrap_or(false);
        checks.push((
            if current { "PASS" } else { "WARN" }.into(),
            "activation plugin".into(),
            if current {
                format!("automatic EOS activation is installed in {}", ws.display())
            } else {
                format!("not installed or out of date in {}; run: eos open {}", ws.display(), ws.display())
            },
        ));
    }

    // 8. install location is independent of user data
    let install_dir = cfg
        .versions
        .iter()
        .find(|v| Some(&v.version) == cfg.active_version.as_ref())
        .map(|v| PathBuf::from(&v.dir));
    let independent = install_dir
        .as_ref()
        .map(|d| {
            let s = d.to_string_lossy().to_lowercase();
            !s.starts_with(&ws.to_string_lossy().to_lowercase())
        })
        .unwrap_or(false);
    checks.push((
        if independent { "PASS" } else { "WARN" }.into(),
        "install/project separation".into(),
        match &install_dir {
            Some(d) => format!("install {} vs workspace {}", d.display(), ws.display()),
            None => "no install to compare".into(),
        },
    ));

    if as_json {
        let verdict = if failures == 0 { "PRODUCT_READY" } else { "NOT_PRODUCT_READY" };
        let body = json!({
            "mode": "product",
            "verdict": verdict,
            "failures": failures,
            "checks": checks.iter().map(|(s, n, d)| json!({"status": s, "check": n, "detail": d})).collect::<Vec<_>>(),
        });
        say!("{}", serde_json::to_string_pretty(&body).unwrap());
    } else {
        say!("EOS doctor (product) — installable-product readiness");
        for (status, name, detail) in &checks {
            say!("  {status:<4} {name:<26} {detail}");
        }
        say!(
            "\n{}: {} blocking item(s)",
            if failures == 0 { "PRODUCT READY" } else { "NOT PRODUCT READY" },
            failures
        );
    }
    if failures == 0 {
        0
    } else {
        1
    }
}

// -------------------------------------------------------------------- open

fn cmd_open(args: &Args, engine: &Engine) -> i32 {
    let target = match args.positionals.first() {
        Some(p) => resolve_workspace(Some(Path::new(p))),
        None => resolve_workspace(None),
    };
    if !target.is_dir() {
        warn!("BLOCKED: not a directory: {}", target.display());
        return 2;
    }
    let dry_run = args.has("print") || args.has("dry-run");
    let integrate = !args.has("no-integrate");

    let detected = hosts::detect();
    let host = match args.value("host") {
        Some(id) => match hosts::get(&id) {
            Some(h) => h,
            None => {
                warn!(
                    "BLOCKED: unknown host '{id}'. Known: {}",
                    hosts::HOSTS.iter().map(|h| h.id).collect::<Vec<_>>().join(", ")
                );
                return 2;
            }
        },
        None => {
            let pick = detected
                .iter()
                .filter(|d| d.detected)
                .filter_map(|d| hosts::get(&d.id))
                .next();
            match pick {
                Some(h) => h,
                None => {
                    warn!(
                        "BLOCKED: no supported host is installed. Install one of: {}",
                        hosts::HOSTS
                            .iter()
                            .map(|h| format!("{} ({})", h.label, h.binaries[0]))
                            .collect::<Vec<_>>()
                            .join(", ")
                    );
                    return 1;
                }
            }
        }
    };

    // project identity: written only where EOS owns the file (`<project>/.eos/`)
    let mut identity = workspace::read_project(&target);
    let mut identity_created = false;
    if identity.is_none() && integrate && !dry_run {
        match workspace::ensure(&target, None, VERSION) {
            Ok((cfg, created)) => {
                identity = Some(cfg);
                identity_created = created;
            }
            Err(e) => {
                warn!("BLOCKED: cannot establish project identity: {e}");
                return 1;
            }
        }
    }

    let integration = if integrate {
        hosts::integrate(&engine.root, &engine.python, &target, host, dry_run)
    } else {
        hosts::Integration {
            host: host.id.to_string(),
            status: "SKIPPED".into(),
            actions: Vec::new(),
            detail: "--no-integrate: no adapter files written".into(),
        }
    };

    if integrate && !dry_run && integration.status == "DONE" {
        let _ = workspace::bind_host(&target, host.id);
        // Registering is a convenience index, not the identity, so a failure
        // here must not fail the open.
        let _ = workspace::add(&target, None, VERSION);
    }

    let launch = hosts::launch_command(host, &target);
    let caps = serde_json::to_value((host.caps)()).unwrap_or(json!({}));

    if args.json() || args.has("print") {
        let body = json!({
            "host": host.id,
            "host_label": host.label,
            "cwd": target.display().to_string(),
            "project_id": identity.as_ref().map(|c| c.id.clone()),
            "project_name": identity.as_ref().map(|c| c.name.clone()),
            "identity_created": identity_created,
            "integration": integration,
            "capabilities": caps,
            "host_verification": host.verification,
            "launch": match &launch {
                Ok((bin, argv, cwd)) => json!({
                    "program": bin.display().to_string(),
                    "args": argv,
                    "cwd": cwd.display().to_string(),
                }),
                Err(e) => json!({"error": e}),
            },
        });
        say!("{}", serde_json::to_string_pretty(&body).unwrap());
        return 0;
    }

    say!("EOS open — {} in {}", host.label, target.display());
    say!("  project id : {}", identity.as_ref().map(|c| c.id.clone()).unwrap_or_else(|| "<unregistered>".into()));
    say!("  integration: {} — {}", integration.status, integration.detail);
    for action in &integration.actions {
        say!("    {:<8} {}  ({})", action.status, action.path, action.detail);
    }
    match launch {
        Ok((bin, argv, cwd)) => {
            if dry_run {
                say!("  would run  : {} {} (cwd {})", bin.display(), argv.join(" "), cwd.display());
                return 0;
            }
            say!("  launching  : {}", bin.display());
            match hosts::spawn_host(&bin, &argv, &cwd) {
                Ok(status) => status.code().unwrap_or(0),
                Err(e) => {
                    warn!("BLOCKED: {e}");
                    1
                }
            }
        }
        Err(e) => {
            warn!("BLOCKED: {e}");
            1
        }
    }
}

// --------------------------------------------------------------- workspaces

fn cmd_workspace(args: &Args) -> i32 {
    let sub = args.positionals.first().map(String::as_str).unwrap_or("list");
    match sub {
        "add" => {
            let Some(path) = args.positionals.get(1) else {
                warn!("usage: eos workspace add <path> [--name N]");
                return 2;
            };
            match workspace::add(Path::new(path), args.value("name").as_deref(), VERSION) {
                Ok((entry, created)) => {
                    if args.json() {
                        say!("{}", serde_json::to_string_pretty(&json!({
                            "workspace": entry, "identity_created": created
                        })).unwrap());
                    } else {
                        say!("PASS: workspace {} ({})", entry.name, entry.path);
                        say!("  project id: {}", entry.project_id);
                        say!(
                            "  identity  : {}",
                            if created { "created .eos/project.json" } else { "already present" }
                        );
                    }
                    0
                }
                Err(e) => {
                    warn!("BLOCKED: {e}");
                    1
                }
            }
        }
        "list" => {
            let missing = workspace::mark_missing();
            let reg = workspace::load_registry();
            if args.json() {
                say!("{}", serde_json::to_string_pretty(&reg).unwrap());
            } else {
                say!(
                    "EOS workspaces — {} registered ({missing} missing), registry {}",
                    reg.workspaces.len(),
                    paths::workspaces_path().display()
                );
                for w in &reg.workspaces {
                    let identity = workspace::read_project(Path::new(&w.path));
                    say!(
                        "  {:<28} {:<10} {}{}",
                        w.name,
                        identity.map(|c| c.id[..8.min(c.id.len())].to_string()).unwrap_or_else(|| "<none>".into()),
                        w.path,
                        if w.missing { "  [MISSING]" } else { "" }
                    );
                }
            }
            0
        }
        "remove" => {
            let Some(sel) = args.positionals.get(1) else {
                warn!("usage: eos workspace remove <id|path>");
                return 2;
            };
            match workspace::remove(sel) {
                Ok(entry) => {
                    say!(
                        "PASS: removed {} from the registry ({})",
                        entry.name, entry.path
                    );
                    say!("  the project directory and its .eos/project.json were not touched");
                    0
                }
                Err(e) => {
                    warn!("BLOCKED: {e}");
                    1
                }
            }
        }
        "relocate" => {
            let (Some(id), Some(new_path)) = (args.positionals.get(1), args.positionals.get(2))
            else {
                warn!("usage: eos workspace relocate <id|old-path> <new-path>");
                return 2;
            };
            match workspace::relocate(id, Path::new(new_path)) {
                Ok((entry, cfg)) => {
                    if args.json() {
                        say!(
                            "{}",
                            serde_json::to_string_pretty(&json!({"workspace": entry, "project": cfg}))
                                .unwrap()
                        );
                    } else {
                        say!("PASS: workspace identity preserved across the move");
                        say!("  id         : {}", entry.project_id);
                        say!("  new path   : {}", entry.path);
                        say!("  identity   : .eos/project.json id {} unchanged", cfg.id);
                    }
                    0
                }
                Err(e) => {
                    warn!("BLOCKED: {e}");
                    1
                }
            }
        }
        other => {
            warn!("unknown workspace subcommand: {other}");
            warn!("usage: eos workspace add|list|remove|relocate");
            2
        }
    }
}

// -------------------------------------------------------------------- hosts

fn cmd_hosts(args: &Args) -> i32 {
    let sub = args.positionals.first().map(String::as_str).unwrap_or("detect");
    let detected = hosts::detect();
    match sub {
        "detect" | "doctor" => {
            if args.json() {
                let body = json!({
                    "hosts": detected,
                    "note": "capabilities are declared per host; a false value means EOS does not \
                             claim that interface for that host",
                });
                say!("{}", serde_json::to_string_pretty(&body).unwrap());
                return 0;
            }
            say!("EOS hosts — installed and declared capability");
            let mut supported = 0usize;
            for d in &detected {
                let binary = d.binary.clone().unwrap_or_else(|| "<not installed>".into());
                say!("\n  {} ({})  {}", d.label, d.id, binary);
                say!(
                    "    agents={} subagents={} permissions={} skills={} hooks={} mcp={} rules={}",
                    d.caps.agents,
                    d.caps.subagents,
                    d.caps.permission_enforcement,
                    d.caps.skills,
                    d.caps.hooks,
                    d.caps.mcp,
                    d.caps.rules
                );
                say!("    evidence: {}", d.verification);
                if d.detected && d.caps.agents {
                    supported += 1;
                }
            }
            say!("\n{supported} host(s) can receive delegated agent work");
            if sub == "doctor" && supported == 0 {
                say!("NOT READY: no host exposes an agent interface");
                return 1;
            }
            0
        }
        other => {
            warn!("unknown hosts subcommand: {other}");
            2
        }
    }
}

// ------------------------------------------------------------------ install

/// `eos skills resolve|explain "<task>"` is the ergonomic form of the engine's
/// `--task <text>`. The engine's own contract is untouched; only the product
/// surface accepts the shorter spelling, and an explicit `--task` always wins.
fn skills_argv(forwarded: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::with_capacity(forwarded.len() + 2);
    out.extend(forwarded.iter().cloned());
    let sub = forwarded.first().map(String::as_str).unwrap_or("");
    if !matches!(sub, "resolve" | "explain") {
        return out;
    }
    if out.iter().any(|a| a == "--task" || a.starts_with("--task=")) {
        return out;
    }
    if let Some(pos) = out.iter().skip(1).position(|a| !a.starts_with('-')) {
        out.insert(pos + 1, "--task".to_string());
    }
    out
}

/// Install the program files.
///
/// This command deliberately does not need an engine: installing the engine is
/// how a machine gets one in the first place. A CLI that refused to run
/// `eos install` because no engine was installed yet could never bootstrap a
/// fresh machine.
fn cmd_install(args: &Args) -> i32 {
    let version = args.value("version").unwrap_or_else(|| VERSION.to_string());
    let force = args.has("force");

    if let Some(target) = args.value("local") {
        // Canonical repository -> workspace control plane.
        let source = match args.value("source") {
            Some(s) => PathBuf::from(s),
            None => match install::resolve_source(None) {
                Ok(s) => s,
                Err(e) => {
                    warn!("BLOCKED: {e}");
                    return 1;
                }
            },
        };
        match install::install_local(&source, Path::new(&target), &version, force) {
            Ok(report) => {
                if args.json() {
                    say!("{}", serde_json::to_string_pretty(&report).unwrap());
                } else {
                    say!("PASS: local install into {}", report.target);
                    say!("  mode       : {} (in-place workspace layout)", report.runtime_mode);
                    say!("  written    : {} file(s), {} already identical", report.written, report.unchanged);
                    if let Some(dir) = &report.backup_dir {
                        say!("  backed up  : {} file(s) -> {}", report.backed_up.len(), dir);
                    }
                    say!("  manifest   : {}/.eos-install.json", report.target);
                }
                0
            }
            Err(e) => {
                warn!("BLOCKED: {e}");
                1
            }
        }
    } else {
        let source = match args.value("source") {
            Some(s) => PathBuf::from(s),
            None => match install::resolve_source(None) {
                Ok(s) => s,
                Err(e) => {
                    warn!("BLOCKED: {e}");
                    return 1;
                }
            },
        };
        match install::install(&source, &version, args.has("add-to-path"), force) {
            Ok(report) => {
                let mut log = vec![
                    "status: PASS".to_string(),
                    format!("version dir: {}", report.version_dir),
                    format!("engine: {} file(s), manifest {}", report.engine_files, report.engine_manifest_sha256),
                    format!("runtime: {}", report.runtime_mode),
                    format!("launcher: {}", report.bin),
                ];
                if let Some(entry) = &report.path_entry {
                    log.push(format!("PATH: {entry}"));
                } else {
                    log.push(format!("PATH: not modified ({})", paths::bin_dir().display()));
                }
                install::write_install_log(&version, &log);
                if args.json() {
                    say!("{}", serde_json::to_string_pretty(&report).unwrap());
                } else {
                    say!("PASS: installed eos {version}");
                    say!("  version dir: {}", report.version_dir);
                    say!("  engine     : {} file(s), manifest {}", report.engine_files, &report.engine_manifest_sha256[..16]);
                    say!("  runtime    : {}", report.runtime_mode);
                    say!("  launcher   : {}", report.bin);
                    if let Some(entry) = &report.path_entry {
                        say!("  PATH       : {entry}");
                    } else {
                        say!(
                            "  PATH       : not modified. Add {} to PATH, or re-run with --add-to-path",
                            paths::bin_dir().display()
                        );
                    }
                }
                0
            }
            Err(e) => {
                install::write_install_log(
                    &version,
                    &["status: BLOCKED".to_string(), format!("reason: {e}")],
                );
                warn!("BLOCKED: {e}");
                1
            }
        }
    }
}

fn cmd_uninstall(args: &Args) -> i32 {
    match install::uninstall(args.has("purge-config"), args.has("keep-versions")) {
        Ok(actions) => {
            if args.json() {
                say!("{}", serde_json::to_string_pretty(&json!({"actions": actions})).unwrap());
            } else {
                say!("PASS: eos uninstalled");
                for a in &actions {
                    say!("  - {a}");
                }
            }
            0
        }
        Err(e) => {
            warn!("BLOCKED: {e}");
            1
        }
    }
}

fn cmd_repair(args: &Args) -> i32 {
    let source = match args.value("source") {
        Some(s) => PathBuf::from(s),
        None => match install::resolve_source(None) {
            Ok(s) => s,
            Err(e) => {
                warn!("BLOCKED: {e}");
                return 1;
            }
        },
    };
    match install::repair(&source, args.value("version").as_deref()) {
        Ok((verified, restored, remaining)) => {
            say!("PASS: repair complete");
            say!("  verified files : {verified}");
            say!("  restored       : {restored}");
            say!("  still broken   : {remaining}");
            if remaining > 0 {
                warn!("BLOCKED: {remaining} file(s) could not be restored from {source:?}");
                return 1;
            }
            0
        }
        Err(e) => {
            warn!("BLOCKED: {e}");
            1
        }
    }
}

fn cmd_update(args: &Args) -> i32 {
    let version = args.value("version").unwrap_or_else(|| VERSION.to_string());
    let source = match args.value("source") {
        Some(s) => PathBuf::from(s),
        None => match install::resolve_source(None) {
            Ok(s) => s,
            Err(e) => {
                warn!("BLOCKED: {e}");
                return 1;
            }
        },
    };
    match install::update(&source, &version, args.has("force")) {
        Ok(report) => {
            say!("PASS: updated to {version} (previous kept for rollback)");
            say!("  version dir: {}", report.version_dir);
            say!("  engine     : {} file(s)", report.engine_files);
            0
        }
        Err(e) => {
            warn!("BLOCKED: {e}");
            1
        }
    }
}

fn cmd_rollback() -> i32 {
    match install::rollback() {
        Ok(version) => {
            say!("PASS: rolled back to {version}");
            0
        }
        Err(e) => {
            warn!("BLOCKED: {e}");
            1
        }
    }
}

fn main() -> ExitCode {
    let raw: Vec<String> = env::args().skip(1).collect();
    if raw.is_empty() || raw[0] == "help" || raw[0] == "--help" || raw[0] == "-h" {
        usage();
        return ExitCode::SUCCESS;
    }
    if raw[0] == "version" || raw[0] == "--version" || raw[0] == "-v" {
        // The build version and the active installed version can differ after
        // an update or a rollback; reporting only one of them would hide which
        // product is actually answering.
        match paths::load_config().active_version {
            Some(active) if active != VERSION => say!("eos {VERSION} (active: {active})"),
            _ => say!("eos {VERSION}"),
        }
        return ExitCode::SUCCESS;
    }

    let args = Args::parse(&raw[1..]);
    let command = raw[0].as_str();
    // Engine commands are forwarded verbatim: re-serialising parsed flags would
    // silently drop them.
    let forwarded: Vec<String> = raw[1..].to_vec();

    let code = match command {
        "workspace" => cmd_workspace(&args),
        "hosts" => cmd_hosts(&args),
        "uninstall" => cmd_uninstall(&args),
        "repair" => cmd_repair(&args),
        "update" => cmd_update(&args),
        "rollback" => cmd_rollback(),
        // Install runs before the engine is required: a machine with no engine
        // is exactly the machine that has to be able to install one.
        "install" => cmd_install(&args),
        _ => {
            let engine = match load_engine() {
                Ok(e) => e,
                Err(msg) => {
                    warn!("BLOCKED: {msg}");
                    return ExitCode::from(2);
                }
            };
            let ws = resolve_workspace(args.value("root").map(PathBuf::from).as_deref());
            match command {
                "doctor" => {
                    if args.has("product") {
                        doctor_product(&engine, &ws, args.json())
                    } else {
                        doctor_core(&engine, &ws, args.json())
                    }
                }
                "open" => cmd_open(&args, &engine),
                "activate" => {
                    // The activation context for a host session: project, rules,
                    // relevant skills, the smallest competent specialist set and
                    // the gate that will judge the result. This is what a host
                    // plugin injects, so the user never has to pick a mode or an
                    // orchestrator by hand.
                    if forwarded.iter().any(|a| a == "--task" || a.starts_with("--task=")) {
                        run_script(&engine, "eos_activate.py", false, &forwarded, &ws)
                    } else {
                        warn!("usage: eos activate --task \"<task>\" [--project <id>] [--json]");
                        2
                    }
                }
                "skills" => match forwarded.first().map(String::as_str) {
                    Some(cmd @ ("discover" | "doctor" | "resolve" | "explain" | "verify" | "provenance")) => {
                        let _ = cmd;
                        let argv = skills_argv(&forwarded);
                        run_script(&engine, "skill_index.py", false, &argv, &ws)
                    }
                    _ => {
                        warn!("usage: eos skills discover|resolve|explain|doctor|verify|provenance");
                        2
                    }
                },
                "gate" => match forwarded.first().map(String::as_str) {
                    Some(cmd @ ("run" | "verify" | "list" | "show")) => {
                        let _ = cmd;
                        run_script(&engine, "quality_gate.py", false, &forwarded, &ws)
                    }
                    _ => {
                        warn!("usage: eos gate run|verify|list|show");
                        2
                    }
                },
                "promote" => match forwarded.first().map(String::as_str) {
                    Some("verify") => run_script(&engine, "promote.py", false, &forwarded, &ws),
                    _ => {
                        let mut argv = vec!["promote".to_string()];
                        argv.extend(forwarded.iter().cloned());
                        run_script(&engine, "promote.py", false, &argv, &ws)
                    }
                },
                "graph" => match forwarded.first().map(String::as_str) {
                    Some(cmd @ ("report" | "fallback" | "invalidate")) => {
                        let _ = cmd;
                        run_script(&engine, "graph_freshness.py", false, &forwarded, &ws)
                    }
                    _ => {
                        warn!("usage: eos graph report|fallback|invalidate");
                        2
                    }
                },
                "scan" => run_script(&engine, "secret_scan.py", false, &forwarded, &ws),
                "publish" => match forwarded.first().map(String::as_str) {
                    Some(cmd @ ("sync" | "verify" | "status")) => {
                        let _ = cmd;
                        run_script(&engine, "publish.py", false, &forwarded, &ws)
                    }
                    _ => {
                        warn!("usage: eos publish sync|verify|status");
                        2
                    }
                },
                "agents" => match forwarded.first().map(String::as_str) {
                    Some("install") => run_script(&engine, "agent_install.py", false, &forwarded, &ws),
                    Some("status") => run_script(&engine, "agent_install.py", false, &["status".to_string()], &ws),
                    Some("uninstall") => run_script(&engine, "agent_install.py", false, &forwarded, &ws),
                    Some(cmd @ ("list" | "explain")) => {
                        let _ = cmd;
                        run_script(&engine, "agent_install.py", false, &forwarded, &ws)
                    }
                    // `test` and `verify` are the same proof: the live runtime's
                    // view of every agent, not the files EOS wrote.
                    Some("test") | Some("verify") => {
                        run_script(&engine, "agent_dispatch_test.py", false, &forwarded[1..], &ws)
                    }
                    Some("permissions") => {
                        let mut argv = vec!["check-contracts".to_string()];
                        argv.extend(forwarded.iter().skip(1).cloned());
                        run_script(&engine, "runtime_permissions.py", false, &argv, &ws)
                    }
                    _ => {
                        warn!("usage: eos agents install|status|uninstall|list|explain|test|verify|permissions");
                        2
                    }
                },
                "orchestrators" => match forwarded.first().map(String::as_str) {
                    Some(cmd @ ("discover" | "doctor" | "test")) => {
                        let _ = cmd;
                        run_script(&engine, "orchestrator_registry.py", false, &forwarded, &ws)
                    }
                    Some("run") => {
                        run_script(&engine, "orchestrator_runtime.py", false, &forwarded, &ws)
                    }
                    _ => {
                        warn!("usage: eos orchestrators discover|doctor|test [--self-test] [--provider <id>] | run --provider <id> --plan <file> [--workspace DIR]");
                        2
                    }
                },
                "route" => run_script(&engine, "route_task.py", true, &forwarded, &ws),
                "snapshot" => match forwarded.first().map(String::as_str) {
                    Some(cmd @ ("create" | "verify" | "restore-test" | "restore")) => {
                        let _ = cmd;
                        run_script(&engine, "snapshot.py", false, &forwarded, &ws)
                    }
                    _ => {
                        warn!("usage: eos snapshot create|verify|restore-test|restore");
                        2
                    }
                },
                "guardian" => match forwarded.first().map(String::as_str) {
                    Some(cmd @ ("scan" | "map" | "audit" | "verify" | "status")) => {
                        let _ = cmd;
                        run_script(&engine, "guardian.py", false, &forwarded, &ws)
                    }
                    _ => {
                        warn!("usage: eos guardian scan|map|audit|verify|status");
                        2
                    }
                },
                other => {
                    warn!("unknown command: {other}");
                    usage();
                    2
                }
            }
        }
    };

    ExitCode::from(u8::try_from(code.clamp(0, 255)).unwrap_or(1))
}
