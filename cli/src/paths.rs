//! Path, layout and file-primitive resolution.
//!
//! Product layout (all four must stay separate or the product cannot be
//! installed, updated or removed cleanly):
//!
//!   program files   <prefix>/eos.exe, <prefix>/engine/**, <prefix>/runtime/python/**
//!   user data       %EOS_HOME%/                      (default ~/.eos)
//!   program state   %EOS_HOME%/versions/<v>/         installed versions
//!   project config  <project>/.eos/project.json      identity + host binding
//!
//! Project identity is a UUID stored in the project, never a path. That is what
//! keeps `eos workspace relocate` lossless.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::env;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub const SCHEMA: u32 = 1;

pub fn home_dir() -> PathBuf {
    env::var_os("USERPROFILE")
        .or_else(|| env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// User data root. Overridable so tests and CI never touch the real profile.
pub fn eos_home() -> PathBuf {
    if let Some(v) = env::var_os("EOS_HOME") {
        return PathBuf::from(v);
    }
    home_dir().join(".eos")
}

pub fn bin_dir() -> PathBuf {
    eos_home().join("bin")
}

pub fn state_dir() -> PathBuf {
    eos_home().join("state")
}

pub fn versions_dir() -> PathBuf {
    eos_home().join("versions")
}

pub fn evidence_dir() -> PathBuf {
    eos_home().join("evidence")
}

pub fn config_path() -> PathBuf {
    eos_home().join("config.json")
}

pub fn workspaces_path() -> PathBuf {
    eos_home().join("workspaces.json")
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct VersionRecord {
    pub version: String,
    pub dir: String,
    pub installed_at: String,
    pub engine_files: usize,
    pub engine_manifest_sha256: String,
    pub runtime_mode: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Config {
    #[serde(default)]
    pub schema: u32,
    #[serde(default)]
    pub active_version: Option<String>,
    #[serde(default)]
    pub previous_version: Option<String>,
    #[serde(default)]
    pub versions: Vec<VersionRecord>,
    #[serde(default)]
    pub path_entry_added: bool,
    #[serde(default)]
    pub path_entry: Option<String>,
}

pub fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    let text = fs::read_to_string(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("invalid JSON in {}: {e}", path.display()))
}

pub fn read_json_or_default<T: for<'de> Deserialize<'de> + Default>(path: &Path) -> T {
    read_json(path).unwrap_or_default()
}

/// Write via a temp file + rename: a crash never leaves a half-written config.
pub fn write_json<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("cannot create {}: {e}", parent.display()))?;
    }
    let text = serde_json::to_string_pretty(value).map_err(|e| format!("serialize: {e}"))?;
    let tmp = path.with_extension("tmp");
    {
        let mut fh = fs::File::create(&tmp).map_err(|e| format!("cannot write {}: {e}", tmp.display()))?;
        fh.write_all(text.as_bytes()).map_err(|e| format!("write: {e}"))?;
        fh.write_all(b"\n").map_err(|e| format!("write: {e}"))?;
        fh.sync_all().ok();
    }
    fs::rename(&tmp, path).map_err(|e| format!("cannot replace {}: {e}", path.display()))
}

pub fn load_config() -> Config {
    let mut cfg: Config = read_json_or_default(&config_path());
    if cfg.schema == 0 {
        cfg.schema = SCHEMA;
    }
    cfg
}

pub fn save_config(cfg: &Config) -> Result<(), String> {
    write_json(&config_path(), cfg)
}

pub fn sha256_file(path: &Path) -> Result<String, String> {
    let mut fh = fs::File::open(path).map_err(|e| format!("cannot open {}: {e}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = fh.read(&mut buf).map_err(|e| format!("read: {e}"))?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(format!("{:X}", hasher.finalize()))
}

pub fn sha256_bytes(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:X}", hasher.finalize())
}

pub fn now_iso() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0) as i64;
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (h, mi, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    // Howard Hinnant's civil_from_days.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}T{h:02}:{mi:02}:{s:02}Z")
}

pub fn exe_dir() -> PathBuf {
    env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Directories that contain `tools/quality_gate.py`.
pub fn candidate_engines() -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    let mut push = |p: PathBuf| {
        let key = p.to_string_lossy().to_lowercase();
        if !out.iter().any(|x| x.to_string_lossy().to_lowercase() == key) {
            out.push(p);
        }
    };

    if let Some(v) = env::var_os("EOS_ENGINE") {
        push(PathBuf::from(v));
    }
    let cfg = load_config();
    if let Some(active) = cfg.active_version.as_ref() {
        if let Some(rec) = cfg.versions.iter().find(|v| &v.version == active) {
            push(PathBuf::from(&rec.dir).join("engine"));
        }
    }
    let exe = exe_dir();
    push(exe.join("engine"));
    push(exe.join("..").join("engine"));

    // Nearest workspace first, then the executable's own ancestors. The working
    // directory is the strongest signal of *which* control plane the user means;
    // a sibling source checkout must not silently win over it.
    let cwd = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    for anc in cwd.ancestors() {
        push(anc.join(".agents"));
        push(anc.join("engine"));
    }
    for anc in exe.ancestors().skip(1) {
        push(anc.join(".agents"));
        push(anc.join("engine"));
    }
    if let Some(root) = env::var_os("EOS_ROOT") {
        let root = PathBuf::from(root);
        push(root.join(".agents"));
        push(root.join("engine"));
    }
    out
}

pub fn is_engine(dir: &Path) -> bool {
    dir.join("tools").join("quality_gate.py").is_file()
        && dir.join("router").join("route_task.py").is_file()
}

pub fn resolve_engine() -> Result<PathBuf, String> {
    let candidates = candidate_engines();
    for c in &candidates {
        let canon = fs::canonicalize(c).unwrap_or_else(|_| c.clone());
        if is_engine(&canon) {
            return Ok(clean(&canon));
        }
    }
    Err(format!(
        "no EOS engine found. Looked in {} location(s) starting from {}. \
         Run `eos install` or set EOS_ENGINE to a directory containing tools/quality_gate.py.",
        candidates.len(),
        exe_dir().display()
    ))
}

/// Interpreter that will run the Python engine.
///
/// Order matters: a bundled runtime must win, so a machine with an unrelated or
/// broken system Python still works.
pub fn resolve_python(engine: &Path) -> Result<(PathBuf, String), String> {
    if let Some(v) = env::var_os("EOS_PYTHON") {
        let p = PathBuf::from(v);
        if p.is_file() {
            return Ok((p, "env:EOS_PYTHON".into()));
        }
        return Err(format!("EOS_PYTHON is set but not a file: {}", p.display()));
    }

    let prefix = engine.parent().unwrap_or_else(|| Path::new("."));
    for candidate in [
        prefix.join("runtime").join("python").join("python.exe"),
        prefix.join("runtime").join("python").join("bin").join("python3"),
        prefix.join("runtime").join("python.exe"),
    ] {
        if candidate.is_file() {
            return Ok((candidate, "bundled".into()));
        }
    }

    for name in ["python", "python3", "py"] {
        if which(name).is_some() {
            return Ok((PathBuf::from(name), "system".into()));
        }
    }
    Err("no Python interpreter: no bundled runtime and none on PATH. \
         This is the packaging defect, not a user error."
        .into())
}

/// Windows returns `\\?\C:\...` for canonicalised paths. That is correct for the
/// filesystem but wrong for a report a human has to read, so every path that
/// leaves EOS is normalised here.
pub fn clean(path: &Path) -> PathBuf {
    let text = path.display().to_string();
    let stripped = text
        .strip_prefix(r"\\?\")
        .or_else(|| text.strip_prefix(r"//?/"))
        .map(|s| s.to_string())
        .unwrap_or(text);
    PathBuf::from(stripped)
}

pub fn which(name: &str) -> Option<PathBuf> {
    let path_var = env::var_os("PATH")?;
    let exts: Vec<String> = env::var("PATHEXT")
        .unwrap_or_else(|_| ".EXE;.CMD;.BAT;.COM".into())
        .split(';')
        .map(|s| s.to_string())
        .collect();
    for dir in env::split_paths(&path_var) {
        // On Windows a file without an extension is not executable: npm-style
        // launchers ship an extensionless POSIX script next to `name.cmd` and
        // `name.ps1`, and picking the script produced
        // "not a valid Win32 application" at launch time.
        if !cfg!(windows) {
            let direct = dir.join(name);
            if direct.is_file() {
                return Some(direct);
            }
        }
        for ext in &exts {
            let candidate = dir.join(format!("{name}{}", ext.to_lowercase()));
            if candidate.is_file() {
                return Some(candidate);
            }
            let candidate = dir.join(format!("{name}{ext}"));
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

pub fn human_bytes(n: u64) -> String {
    let units = ["B", "KB", "MB", "GB"];
    let mut value = n as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit < units.len() - 1 {
        value /= 1024.0;
        unit += 1;
    }
    format!("{value:.1} {}", units[unit])
}
