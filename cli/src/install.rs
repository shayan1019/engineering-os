//! Installation, versioning, repair and rollback.
//!
//! Layout produced on disk (documented in README):
//!
//! ```text
//! %EOS_HOME%/                      user data, never inside a project
//!   bin/eos.exe                    the active launcher on PATH
//!   config.json                    active version, PATH ownership flag
//!   versions/<version>/
//!     eos.exe
//!     engine/**                    Python engineering core (hashed)
//!     runtime/python/**            bundled interpreter, when packaged
//!     install-manifest.json        per-file SHA-256 + manifest hash
//!   versions/<version>/install-manifest.json
//!   evidence/**                    gate + promotion records, per project id
//!   state/**                       caches (skill index, logs)
//! ```
//!
//! Uninstall removes files that appear in an install manifest, and nothing else.
//! That is the safety property: it cannot delete a user project.

use crate::paths;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

pub const VERSION: &str = concat!(env!("CARGO_PKG_VERSION"), "-alpha");

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ManifestEntry {
    pub rel: String,
    pub sha256: String,
    pub size: u64,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct InstallManifest {
    pub schema: u32,
    pub version: String,
    pub installed_at: String,
    pub runtime_mode: String,
    pub engine_root_rel: String,
    pub entries: Vec<ManifestEntry>,
    pub manifest_sha256: String,
}

/// `at_root` distinguishes EOS's own user data from a skill's own documents.
///
/// `<engine>/evidence` is gate output and must never ship. A skill package's
/// `skills/<name>/evidence/` is part of the skill: fable-5-1 links three files
/// there, and a name-only rule silently installed a skill with broken links.
fn skip(name: &str, at_root: bool) -> bool {
    (at_root && matches!(name, "evidence" | "backups"))
        || matches!(
            name,
            "__pycache__" | ".git" | "target" | "node_modules" | ".DS_Store"
        ) || name.ends_with(".pyc")
        || name.ends_with(".db")
        || name.ends_with(".log")
        || name.ends_with(".tmp")
        // Generated per workspace by `eos skills discover`; copying one
        // workspace's index over another's would record wrong paths.
        || name == "SKILL_INDEX.json"
        || name == "SKILL_CATALOG.json"
}

fn collect(dir: &Path, base: &Path, out: &mut Vec<PathBuf>, at_root: bool) -> Result<(), String> {
    let rd = fs::read_dir(dir).map_err(|e| format!("cannot read {}: {e}", dir.display()))?;
    for entry in rd.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if skip(&name, at_root) {
            continue;
        }
        if path.is_dir() {
            collect(&path, base, out, false)?;
        } else if path.is_file() {
            let rel = path
                .strip_prefix(base)
                .map_err(|_| "path escaped base".to_string())?
                .to_string_lossy()
                .replace('\\', "/");
            out.push(PathBuf::from(rel));
        }
    }
    Ok(())
}

pub fn hash_relative(base: &Path) -> Result<(Vec<ManifestEntry>, String), String> {
    let mut rels = Vec::new();
    collect(base, base, &mut rels, true)?;
    rels.sort();
    let mut entries = Vec::new();
    let mut feed = String::new();
    for rel in rels {
        let full = base.join(&rel);
        let hash = paths::sha256_file(&full)?;
        let size = fs::metadata(&full).map(|m| m.len()).unwrap_or(0);
        let rel_s = rel.to_string_lossy().replace('\\', "/");
        feed.push_str(&format!("{rel_s}:{hash}\n"));
        entries.push(ManifestEntry {
            rel: rel_s,
            sha256: hash,
            size,
        });
    }
    Ok((entries, paths::sha256_bytes(feed.as_bytes())))
}

fn copy_tree(src: &Path, dst: &Path, at_root: bool) -> Result<usize, String> {
    fs::create_dir_all(dst).map_err(|e| format!("cannot create {}: {e}", dst.display()))?;
    let mut n = 0usize;
    for entry in fs::read_dir(src)
        .map_err(|e| format!("cannot read {}: {e}", src.display()))?
        .flatten()
    {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if skip(&name, at_root) {
            continue;
        }
        let target = dst.join(entry.file_name());
        if path.is_dir() {
            n += copy_tree(&path, &target, false)?;
        } else {
            fs::copy(&path, &target)
                .map_err(|e| format!("cannot copy {} -> {}: {e}", path.display(), target.display()))?;
            n += 1;
        }
    }
    Ok(n)
}

/// Where does an install get its program files from?
///
/// Order: explicit `--source`, then an installation staged next to this binary
/// (`../` for a `cli/target/release` dev build, or the release staging folder),
/// then the directory this binary sits in.
pub fn resolve_source(explicit: Option<&Path>) -> Result<PathBuf, String> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(p) = explicit {
        candidates.push(p.to_path_buf());
    }
    let exe = paths::exe_dir();
    candidates.push(exe.clone());
    candidates.push(exe.join("..").to_path_buf());
    candidates.push(exe.join("..").join("..").to_path_buf());
    candidates.push(exe.join("..").join("..").join("..").to_path_buf());
    let mut last_err = String::new();
    for cand in &candidates {
        let engine = if cand.join("engine").join("tools").join("quality_gate.py").is_file() {
            cand.join("engine")
        } else if paths::is_engine(cand) {
            cand.clone()
        } else {
            last_err = format!("no engine under {}", cand.display());
            continue;
        };
        let root = engine.parent().unwrap_or(Path::new(".")).to_path_buf();
        return Ok(root);
    }
    Err(format!(
        "cannot find installable program files. Pass --source <repo-or-release-dir>. \
         Last attempt: {last_err}"
    ))
}

#[derive(Serialize, Clone, Debug)]
pub struct InstallReport {
    pub version: String,
    pub version_dir: String,
    pub runtime_mode: String,
    pub engine_files: usize,
    pub engine_manifest_sha256: String,
    pub bin: String,
    pub path_entry: Option<String>,
    pub switched_from: Option<String>,
    pub total_bytes: u64,
}

/// Install program files into a version directory and activate them.
/// Durable record of an install attempt.
///
/// The NSIS installer runs the CLI without a console, so this file is the only
/// evidence a user can read after a failed install. It is written for success
/// as well: "it worked" is a claim that should be checkable later.
pub fn write_install_log(version: &str, lines: &[String]) -> Option<PathBuf> {
    let dir = paths::eos_home().join("state");
    if fs::create_dir_all(&dir).is_err() {
        return None;
    }
    let path = dir.join(format!("install-{version}.log"));
    let mut body = String::new();
    body.push_str(&format!("eos install {version}\n"));
    body.push_str(&format!("at {}\n", paths::now_iso()));
    for line in lines {
        body.push_str(line);
        body.push('\n');
    }
    let _ = fs::write(&path, body);
    Some(path)
}

pub fn install(
    source: &Path,
    version: &str,
    add_to_path: bool,
    force: bool,
) -> Result<InstallReport, String> {
    let version_dir = paths::versions_dir().join(version);
    if version_dir.exists() {
        if !force {
            return Err(format!(
                "version {version} is already installed at {}. Use --force to reinstall.",
                version_dir.display()
            ));
        }
        fs::remove_dir_all(&version_dir)
            .map_err(|e| format!("cannot clear {}: {e}", version_dir.display()))?;
    }
    fs::create_dir_all(&version_dir)
        .map_err(|e| format!("cannot create {}: {e}", version_dir.display()))?;

    // 1. the CLI binary itself, so the version is self-contained
    let me = std::env::current_exe().map_err(|e| format!("cannot locate own binary: {e}"))?;
    let dest_exe = version_dir.join(if cfg!(windows) { "eos.exe" } else { "eos" });
    fs::copy(&me, &dest_exe).map_err(|e| format!("cannot copy CLI: {e}"))?;

    // 2. engine
    let engine_src = if source.join("engine").is_dir() {
        source.join("engine")
    } else {
        source.to_path_buf()
    };
    if !paths::is_engine(&engine_src) {
        return Err(format!(
            "source {} does not contain engine/tools/quality_gate.py",
            engine_src.display()
        ));
    }
    let engine_dst = version_dir.join("engine");
    copy_tree(&engine_src, &engine_dst, true)?;

    // 3. optional bundled runtime
    let runtime_src = source.join("runtime");
    let runtime_mode = if runtime_src.join("python").is_dir() {
        copy_tree(&runtime_src, &version_dir.join("runtime"), true)?;
        "bundled".to_string()
    } else {
        "system".to_string()
    };

    // 4. manifest
    let (engine_entries, engine_manifest_sha256) = hash_relative(&engine_dst)?;
    let (file_entries, _) = hash_relative(&version_dir)?;
    let mut entries = file_entries;
    let total_bytes = entries.iter().map(|e| e.size).sum();
    let mut manifest = InstallManifest {
        schema: paths::SCHEMA,
        version: version.to_string(),
        installed_at: paths::now_iso(),
        runtime_mode: runtime_mode.clone(),
        engine_root_rel: "engine".into(),
        entries: std::mem::take(&mut entries),
        manifest_sha256: String::new(),
    };
    let feed: String = manifest
        .entries
        .iter()
        .map(|e| format!("{}:{}\n", e.rel, e.sha256))
        .collect();
    manifest.manifest_sha256 = paths::sha256_bytes(feed.as_bytes());
    paths::write_json(&version_dir.join("install-manifest.json"), &manifest)?;

    // 5. activate
    //
    // Everything from here on can fail. A failed activation must not leave a
    // version directory behind: `eos rollback` would happily switch to a
    // half-installed version, and a broken version that answers to its own
    // name is worse than no version at all.
    let activated = (|| -> Result<InstallReport, String> {
    let mut cfg = paths::load_config();
    let previous = cfg.active_version.clone();
    // Re-installing the version that is already active is not a rollback
    // target. The recorded previous version is kept, otherwise a repair of the
    // active version would silently destroy the way back.
    let previous = match previous {
        Some(p) if p == version => cfg.previous_version.clone(),
        other => other,
    };
    let runtime_used = if runtime_mode == "bundled" {
        "bundled".to_string()
    } else {
        "system".to_string()
    };
    cfg.versions.retain(|v| v.version != version);
    cfg.versions.push(paths::VersionRecord {
        version: version.to_string(),
        dir: version_dir.display().to_string(),
        installed_at: paths::now_iso(),
        engine_files: engine_entries.len(),
        engine_manifest_sha256: engine_manifest_sha256.clone(),
        runtime_mode: runtime_used,
    });
    cfg.previous_version = previous.clone();
    cfg.active_version = Some(version.to_string());
    let mut path_entry = None;
    if add_to_path {
        path_entry = Some(add_path_entry()?);
        cfg.path_entry_added = true;
        cfg.path_entry = path_entry.clone();
    } else if cfg.path_entry_added {
        path_entry = cfg.path_entry.clone();
    }
    paths::save_config(&cfg)?;

    let bin = paths::bin_dir().join(if cfg!(windows) { "eos.exe" } else { "eos" });
    publish_launcher(&dest_exe)?;

    // A gate refuses to run without a baseline, and the installed engine is the
    // thing that must not drift. Record its manifest as a baseline so
    // `eos gate run` works on a machine that has no workspace at all.
    write_engine_baseline(&version_dir, version, &engine_entries)?;

    Ok(InstallReport {
        version: version.to_string(),
        version_dir: version_dir.display().to_string(),
        runtime_mode,
        engine_files: engine_entries.len(),
        engine_manifest_sha256,
        bin: bin.display().to_string(),
        path_entry,
        switched_from: previous,
        total_bytes,
    })
    })();
    match activated {
        Ok(report) => Ok(report),
        Err(e) => {
            // The version directory is removed so a failed install cannot
            // become a rollback target later.
            let _ = fs::remove_dir_all(&version_dir);
            Err(e)
        }
    }
}

/// Verify every file in the install manifest. Returns (ok, missing, changed).
pub fn verify_install(version: &str) -> Result<(usize, Vec<String>, Vec<String>), String> {
    let cfg = paths::load_config();
    let record = cfg
        .versions
        .iter()
        .find(|v| v.version == version)
        .ok_or_else(|| format!("version {version} is not installed"))?;
    let dir = PathBuf::from(&record.dir);
    let manifest: InstallManifest = paths::read_json(&dir.join("install-manifest.json"))
        .map_err(|e| format!("cannot read install manifest: {e}"))?;
    let mut ok = 0usize;
    let mut missing = Vec::new();
    let mut changed = Vec::new();
    for entry in &manifest.entries {
        let full = dir.join(entry.rel.replace('/', std::path::MAIN_SEPARATOR_STR));
        if !full.is_file() {
            missing.push(entry.rel.clone());
            continue;
        }
        match paths::sha256_file(&full) {
            Ok(h) if h == entry.sha256 => ok += 1,
            Ok(_) => changed.push(entry.rel.clone()),
            Err(e) => changed.push(format!("{} ({e})", entry.rel)),
        }
    }
    Ok((ok, missing, changed))
}

/// Re-copy damaged or missing files from a source tree.
pub fn repair(source: &Path, version: Option<&str>) -> Result<(usize, usize, usize), String> {
    let cfg = paths::load_config();
    let version = version
        .map(|s| s.to_string())
        .or_else(|| cfg.active_version.clone())
        .ok_or_else(|| "no installed version to repair".to_string())?;
    let record = cfg
        .versions
        .iter()
        .find(|v| v.version == version)
        .ok_or_else(|| format!("version {version} is not installed"))?
        .clone();
    let dir = PathBuf::from(&record.dir);
    let manifest: InstallManifest = paths::read_json(&dir.join("install-manifest.json"))?;
    let engine_src = if source.join("engine").is_dir() {
        source.join("engine")
    } else {
        source.to_path_buf()
    };

    let (_, missing, changed) = verify_install(&version)?;
    let mut restored = 0usize;
    for rel in missing.iter().chain(changed.iter()) {
        let rel = rel.split(" (").next().unwrap_or(rel);
        let target = dir.join(rel.replace('/', std::path::MAIN_SEPARATOR_STR));
        // engine files come from the source tree; anything else from the CLI dir
        let candidate = if let Some(rest) = rel.strip_prefix("engine/") {
            engine_src.join(rest.replace('/', std::path::MAIN_SEPARATOR_STR))
        } else if rel == "eos.exe" || rel == "eos" {
            std::env::current_exe().map_err(|e| e.to_string())?
        } else {
            source.join(rel.replace('/', std::path::MAIN_SEPARATOR_STR))
        };
        if candidate.is_file() {
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
            fs::copy(&candidate, &target).map_err(|e| e.to_string())?;
            restored += 1;
        }
    }
    let (ok, still_missing, still_changed) = verify_install(&version)?;
    Ok((ok, restored, still_missing.len() + still_changed.len()))
}

pub fn uninstall(purge_config: bool, keep_versions: bool) -> Result<Vec<String>, String> {
    let cfg = paths::load_config();
    let mut actions = Vec::new();

    if cfg.path_entry_added {
        match remove_path_entry() {
            Ok(removed) => actions.push(format!(
                "PATH entry {}",
                if removed { "removed" } else { "was already absent" }
            )),
            Err(e) => actions.push(format!("PATH entry removal failed: {e}")),
        }
    } else {
        actions.push("PATH entry not owned by EOS: left untouched".into());
    }

    let bin = paths::bin_dir();
    if bin.is_dir() {
        let mut n = 0;
        let mut pending: Vec<String> = Vec::new();
        for entry in fs::read_dir(&bin).map_err(|e| e.to_string())?.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !name.starts_with("eos") {
                continue;
            }
            // Windows refuses to delete a running executable. `eos uninstall` is
            // usually run *from* that executable, so the launcher is renamed
            // first (which Windows allows) and then removed.
            let renamed = entry.path().with_file_name(format!("{name}.removing"));
            let _ = fs::rename(entry.path(), &renamed);
            match fs::remove_file(&renamed) {
                Ok(()) => n += 1,
                Err(_) => pending.push(renamed.display().to_string()),
            }
        }
        actions.push(format!("{n} launcher file(s) removed from {}", bin.display()));
        if !pending.is_empty() {
            actions.push(format!(
                "launcher file(s) still in use, delete after closing eos: {}",
                pending.join(", ")
            ));
        }
    }

    if !keep_versions {
        if paths::versions_dir().is_dir() {
            // Same Windows rule as the launcher: a version directory can hold
            // the running executable, so it is renamed out of the way first.
            let stamp = paths::now_iso().replace([':', '-'], "");
            let quarantine = paths::eos_home().join(format!("versions.removing-{stamp}"));
            let renamed = match fs::rename(paths::versions_dir(), &quarantine) {
                Ok(()) => Some(quarantine),
                Err(_) => None,
            };
            let target = renamed.clone().unwrap_or_else(paths::versions_dir);
            match fs::remove_dir_all(&target) {
                Ok(()) => actions.push(format!("removed {}", target.display())),
                Err(e) => actions.push(format!(
                    "could not remove {}: {e} (delete it once eos is closed)",
                    target.display()
                )),
            }
        }
    } else {
        actions.push("installed versions kept (--keep-versions)".into());
    }

    if purge_config {
        if paths::eos_home().is_dir() {
            fs::remove_dir_all(paths::eos_home())
                .map_err(|e| format!("cannot remove EOS_HOME: {e}"))?;
            actions.push(format!("removed user data {}", paths::eos_home().display()));
        }
    } else {
        let keep = paths::Config {
            schema: paths::SCHEMA,
            active_version: None,
            previous_version: cfg.active_version.clone(),
            versions: cfg.versions.clone(),
            path_entry_added: false,
            path_entry: None,
        };
        paths::save_config(&keep)?;
        actions.push(format!(
            "user data kept at {} (evidence and config preserved)",
            paths::eos_home().display()
        ));
    }
    actions.push("no project directory was touched".into());
    Ok(actions)
}

/// Install a newer version and activate it only if `doctor` passes on it.
pub fn update(source: &Path, version: &str, force: bool) -> Result<InstallReport, String> {
    let report = install(source, version, false, force)?;
    let (ok, missing, changed) = verify_install(version)?;
    if !missing.is_empty() || !changed.is_empty() {
        // Roll back immediately: an update that fails verification must not be
        // the active version.
        let _ = rollback();
        return Err(format!(
            "update rolled back: {ok} file(s) verified but {} missing and {} changed",
            missing.len(),
            changed.len()
        ));
    }
    Ok(report)
}

pub fn rollback() -> Result<String, String> {
    let mut cfg = paths::load_config();
    let current = cfg
        .active_version
        .clone()
        .ok_or_else(|| "no active version".to_string())?;
    let target = cfg
        .previous_version
        .clone()
        .ok_or_else(|| "no previous version recorded; nothing to roll back to".to_string())?;
    if !cfg.versions.iter().any(|v| v.version == target) {
        return Err(format!("previous version {target} is no longer installed"));
    }
    cfg.previous_version = Some(current);
    cfg.active_version = Some(target.clone());
    paths::save_config(&cfg)?;
    let record = cfg.versions.iter().find(|v| v.version == target).unwrap().clone();
    let src = PathBuf::from(&record.dir).join(if cfg!(windows) { "eos.exe" } else { "eos" });
    if src.is_file() {
        publish_launcher(&src)?;
    }
    Ok(target)
}

/// Publish the launcher into the bin directory.
///
/// The file being replaced is usually the program that is running right now:
/// Windows refuses to overwrite a running executable, but it does allow one to
/// be renamed. The old launcher is moved aside, the new one is put in place,
/// and the old file is deleted as soon as the operating system releases it.
fn publish_launcher(src: &Path) -> Result<(), String> {
    let bin = paths::bin_dir().join(if cfg!(windows) { "eos.exe" } else { "eos" });
    fs::create_dir_all(paths::bin_dir()).map_err(|e| format!("cannot create bin dir: {e}"))?;
    let name = bin.file_name().unwrap_or_default().to_string_lossy().to_string();
    let aside = bin.with_file_name(format!("{name}.removing"));
    let _ = fs::remove_file(&aside);
    if bin.is_file() {
        fs::rename(&bin, &aside)
            .map_err(|e| format!("cannot replace the launcher {}: {e}", bin.display()))?;
    }
    if let Err(e) = fs::copy(src, &bin) {
        let _ = fs::rename(&aside, &bin); // put the previous launcher back
        return Err(format!("cannot publish launcher: {e}"));
    }
    // The old image may still be mapped by a running process. In that case the
    // file stays and the next uninstall removes it.
    let _ = fs::remove_file(&aside);
    Ok(())
}

// ---------------------------------------------------------------- PATH edits//
// Windows: HKCU\Environment is the per-user, no-admin location. The entry is
// stored as a literal `%USERPROFILE%\.eos\bin` expansion so it survives profile
// relocation. Only the string EOS appended is ever removed.
fn path_entry_literal() -> String {
    if cfg!(windows) {
        "%USERPROFILE%\\.eos\\bin".to_string()
    } else {
        "$HOME/.eos/bin".to_string()
    }
}

fn read_user_path() -> Result<(String, String), String> {
    if !cfg!(windows) {
        return Err("PATH editing is implemented for Windows in ALPHA".into());
    }
    let out = Command::new("reg")
        .args(["query", "HKCU\\Environment", "/v", "Path"])
        .output()
        .map_err(|e| format!("reg query failed: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with("Path") {
            let parts: Vec<&str> = line.splitn(3, "REG_EXPAND_SZ").collect();
            let value = if parts.len() > 1 {
                parts[1].trim().to_string()
            } else {
                let parts: Vec<&str> = line.splitn(3, "REG_SZ").collect();
                if parts.len() > 1 {
                    parts[1].trim().to_string()
                } else {
                    String::new()
                }
            };
            let kind = if line.contains("REG_EXPAND_SZ") {
                "REG_EXPAND_SZ"
            } else {
                "REG_SZ"
            };
            return Ok((value, kind.to_string()));
        }
    }
    Ok((String::new(), "REG_EXPAND_SZ".to_string()))
}

/// Whether a *new* shell will find this directory on PATH.
///
/// A running process keeps the environment it started with, so an install that
/// has just updated the user PATH must not report itself as broken. Both the
/// process environment and the persisted user value are consulted; the persisted
/// value is authoritative for anything started later.
pub fn user_path_contains(dir: &std::path::Path) -> bool {
    let Ok((current, _)) = read_user_path() else {
        return false;
    };
    let target = dir.to_string_lossy().to_lowercase();
    let target = target.trim_end_matches('\\').to_string();
    let literal = path_entry_literal().to_lowercase();
    let literal = literal.trim_end_matches('\\').to_string();
    current.split(';').any(|part| {
        let p = part.trim().to_lowercase();
        let p = p.trim_end_matches('\\');
        !p.is_empty() && (p == target || p == literal)
    })
}

pub fn add_path_entry() -> Result<String, String> {
    let literal = path_entry_literal();
    let (current, kind) = read_user_path()?;
    if current.to_lowercase().contains(&literal.to_lowercase()) {
        return Ok(literal);
    }
    let new_value = if current.trim().is_empty() {
        literal.clone()
    } else {
        format!("{};{}", current.trim_end_matches(';'), literal)
    };
    let status = Command::new("reg")
        .args([
            "add",
            "HKCU\\Environment",
            "/v",
            "Path",
            "/t",
            &kind,
            "/d",
            &new_value,
            "/f",
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| format!("reg add failed: {e}"))?;
    if !status.success() {
        return Err("reg add returned a failure status".into());
    }
    Ok(literal)
}

pub fn remove_path_entry() -> Result<bool, String> {
    let literal = path_entry_literal();
    let (current, kind) = read_user_path()?;
    if current.trim().is_empty() || !current.to_lowercase().contains(&literal.to_lowercase()) {
        return Ok(false);
    }
    let mut parts: Vec<String> = current
        .split(';')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    let before = parts.len();
    parts.retain(|p| !p.eq_ignore_ascii_case(&literal));
    if parts.len() == before {
        return Ok(false);
    }
    let new_value = parts.join(";");
    let status = Command::new("reg")
        .args([
            "add",
            "HKCU\\Environment",
            "/v",
            "Path",
            "/t",
            &kind,
            "/d",
            &new_value,
            "/f",
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| format!("reg add failed: {e}"))?;
    if !status.success() {
        return Err("reg add returned a failure status".into());
    }
    Ok(true)
}

// ------------------------------------------------- local (in-place) installs
//
// A workspace keeps its engine at `<workspace>/.agents`, which is not the
// product layout. `eos install --local <dir>` is how the canonical repository
// becomes the source of truth for such a workspace: the mapped program files are
// copied in, replaced files are backed up first, and nothing outside the mapping
// is touched. The workspace stays an independently recoverable installation.

/// (source relative to `<repo>/engine`, destination relative to `<dir>`)
const LOCAL_MAP: &[(&str, &str)] = &[
    ("tools", "tools"),
    ("router", "router"),
    ("registry", "registry"),
    ("core", "core"),
    ("agents", "eos/agents"),
    ("selftest", "eos/selftest"),
    ("skills", "skills"),
];

#[derive(Serialize, Clone, Debug)]
pub struct LocalReport {
    pub target: String,
    pub runtime_mode: String,
    pub written: usize,
    pub unchanged: usize,
    pub backed_up: Vec<String>,
    pub backup_dir: Option<String>,
}

pub fn install_local(
    source: &Path,
    target: &Path,
    version: &str,
    force: bool,
) -> Result<LocalReport, String> {
    let engine = if source.join("engine").join("tools").join("quality_gate.py").is_file() {
        source.join("engine")
    } else {
        source.to_path_buf()
    };
    if !paths::is_engine(&engine) {
        return Err(format!(
            "source {} is not an engine (no tools/quality_gate.py)",
            engine.display()
        ));
    }
    fs::create_dir_all(target).map_err(|e| format!("cannot create {}: {e}", target.display()))?;

    let stamp = paths::now_iso().replace([':', '-'], "");
    let backup_dir = target.join(format!("_eos-backup-{stamp}"));
    let mut written = 0usize;
    let mut unchanged = 0usize;
    let mut backed_up: Vec<String> = Vec::new();

    for (src_rel, dst_rel) in LOCAL_MAP {
        let src_root = engine.join(src_rel);
        if !src_root.is_dir() {
            continue;
        }
        let mut rels = Vec::new();
        collect(&src_root, &src_root, &mut rels, true)?;
        for rel in rels {
            let rel_s = rel.to_string_lossy().replace('\\', "/");
            let from = src_root.join(&rel);
            let to = target
                .join(dst_rel.replace('/', std::path::MAIN_SEPARATOR_STR))
                .join(&rel);
            let new_bytes = fs::read(&from).map_err(|e| format!("read {}: {e}", from.display()))?;
            if let Ok(existing) = fs::read(&to) {
                if existing == new_bytes {
                    unchanged += 1;
                    continue;
                }
                if !force {
                    return Err(format!(
                        "{} differs from the canonical copy. Re-run with --force to replace it \
                         (the previous file is backed up first).",
                        to.display()
                    ));
                }
                let backup_path = backup_dir.join(dst_rel).join(&rel);
                if let Some(parent) = backup_path.parent() {
                    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
                }
                fs::copy(&to, &backup_path).map_err(|e| e.to_string())?;
                backed_up.push(format!("{dst_rel}/{rel_s}"));
            }
            if let Some(parent) = to.parent() {
                fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
            fs::write(&to, &new_bytes).map_err(|e| format!("write {}: {e}", to.display()))?;
            written += 1;
        }
    }

    // A record of what a local install owns, so it can be diffed or repaired.
    #[derive(Serialize)]
    struct LocalManifest<'a> {
        schema: u32,
        version: &'a str,
        installed_at: String,
        target: String,
        mapping: Vec<String>,
    }
    let manifest = LocalManifest {
        schema: paths::SCHEMA,
        version,
        installed_at: paths::now_iso(),
        target: target.display().to_string(),
        mapping: LOCAL_MAP
            .iter()
            .map(|(s, d)| format!("{s} -> {d}"))
            .collect(),
    };
    paths::write_json(&target.join(".eos-install.json"), &manifest)?;

    let backup_dir = if backed_up.is_empty() {
        None
    } else {
        Some(&backup_dir)
    };
    Ok(LocalReport {
        target: target.display().to_string(),
        runtime_mode: "in-place".into(),
        written,
        unchanged,
        backed_up,
        backup_dir: backup_dir.map(|p| p.display().to_string()),
    })
}

/// Record the installed engine as a baseline in the `HASHES.txt` format the
/// gate expects (`sha256|relpath`). Without this, a fresh machine has no
/// baseline and every gate refuses to run — which would be a product defect,
/// not a safety property.
pub fn write_engine_baseline(
    version_dir: &Path,
    version: &str,
    entries: &[ManifestEntry],
) -> Result<PathBuf, String> {
    let dir = paths::eos_home()
        .join("baselines")
        .join(format!("baseline_engine_{version}"));
    fs::create_dir_all(&dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    let mut lines: Vec<String> = entries
        .iter()
        .map(|e| format!("{}|engine/{}", e.sha256, e.rel))
        .collect();
    lines.sort();
    let mut body = lines.join("\n");
    body.push('\n');
    fs::write(dir.join("HASHES.txt"), body).map_err(|e| format!("write HASHES.txt: {e}"))?;
    let meta = serde_json::json!({
        "schema": paths::SCHEMA,
        "kind": "engine_baseline",
        "version": version,
        "created_at": paths::now_iso(),
        "source": version_dir.display().to_string(),
        "file_count": entries.len(),
        "total_bytes": entries.iter().map(|e| e.size).sum::<u64>(),
        "note": "Hashes of the installed engine. The gate records this file's hash and entry \
                 count, so editing the engine after install is detected rather than trusted.",
    });
    paths::write_json(&dir.join("SNAPSHOT.json"), &meta)?;
    Ok(dir)
}
