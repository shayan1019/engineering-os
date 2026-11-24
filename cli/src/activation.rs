//! Switching the active version, as one recoverable transaction.
//!
//! A version is more than `config.json`: it is the launcher, the engine
//! directory and the host plugins the user's sessions actually load. Changing
//! some of them and not the others is how a rollback could report PASS while
//! the write guard kept running the version that was rolled back from.
//!
//! Two rules:
//!
//! * **Everything or nothing.** Every file that will change is copied first and
//!   recorded in `state/activation-txn.json`. A failure restores it; a crash
//!   leaves the journal, the guard refuses to run while it exists, and the next
//!   `eos` version command (or `eos recover`) restores it.
//! * **An inactive version is never a newer sibling.** Guards shipped up to
//!   1.0.2 run the greatest directory name under `versions/`. Rolling back to
//!   one of them restores that behaviour too, so any entry that would outrank
//!   the active version is parked in `inactive/`.

use crate::install;
use crate::paths::{self, Config};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::path::{Path, PathBuf};

pub const PLUGINS: [&str; 2] = ["eos-activation.js", "eos-guard.js"];

#[derive(Serialize, Deserialize, Debug)]
struct SavedFile {
    path: PathBuf,
    /// `None`: the file did not exist and is removed on restore.
    backup: Option<PathBuf>,
}

#[derive(Serialize, Deserialize, Debug)]
struct Move {
    from: PathBuf,
    to: PathBuf,
}

#[derive(Serialize, Deserialize, Debug)]
struct Journal {
    schema: u32,
    target: String,
    started_at: String,
    files: Vec<SavedFile>,
    moves: Vec<Move>,
}

/// Holds the OS lock for the whole change. The OS drops it on process exit, so
/// a crash cannot leave a lock behind; it leaves the journal instead.
pub struct Guard {
    _lock: File,
}

pub fn inactive_dir() -> PathBuf {
    paths::eos_home().join("inactive")
}

fn journal_path() -> PathBuf {
    paths::state_dir().join("activation-txn.json")
}

fn backup_dir() -> PathBuf {
    paths::state_dir().join("activation-txn")
}

fn ledger_path() -> PathBuf {
    paths::state_dir().join("plugin-sync.json")
}

pub fn launcher_path() -> PathBuf {
    paths::bin_dir().join(launcher_name())
}

pub fn launcher_name() -> &'static str {
    if cfg!(windows) { "eos.exe" } else { "eos" }
}

pub fn valid_version(version: &str) -> bool {
    version.starts_with(|c: char| c.is_ascii_alphanumeric())
        && version.chars().all(|c| c.is_ascii_alphanumeric() || "._+-".contains(c))
}

/// Take the lock and finish any interrupted change by restoring the old state.
pub fn begin() -> Result<(Guard, bool), String> {
    fs::create_dir_all(paths::state_dir()).map_err(|e| format!("cannot create state dir: {e}"))?;
    let path = paths::state_dir().join("activation.lock");
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(&path)
        .map_err(|e| format!("cannot open {}: {e}", path.display()))?;
    lock.try_lock()
        .map_err(|e| format!("another EOS version change is running ({e})"))?;
    let mut recovered = false;
    if journal_path().exists() {
        let journal: Journal = paths::read_json(&journal_path())
            .map_err(|e| format!("RECOVERY REQUIRED: the activation journal is unreadable: {e}"))?;
        restore(&journal)?;
        recovered = true;
    }
    Ok((Guard { _lock: lock }, recovered))
}

/// A config that exists but cannot be read is an error, never an empty config:
/// treating it as empty would forget every installed version.
pub fn strict_config() -> Result<Config, String> {
    if !paths::config_path().exists() {
        return Ok(paths::load_config());
    }
    let mut cfg: Config = paths::read_json(&paths::config_path())?;
    if cfg.schema == 0 {
        cfg.schema = paths::SCHEMA;
    }
    Ok(cfg)
}

fn restore(journal: &Journal) -> Result<(), String> {
    let mut problems = Vec::new();
    for item in journal.moves.iter().rev() {
        // Only undo a move that happened. Both existing would mean something
        // else recreated the source; overwriting it is not a recovery.
        match (item.from.exists(), item.to.exists()) {
            (false, true) => {
                if let Err(e) = fs::rename(&item.to, &item.from) {
                    problems.push(format!("{} -> {}: {e}", item.to.display(), item.from.display()));
                }
            }
            (true, false) => {}
            (from, _) => problems.push(format!(
                "{} {}",
                item.from.display(),
                if from { "and its new location both exist" } else { "is missing" }
            )),
        }
    }
    for saved in journal.files.iter().rev() {
        let result = match &saved.backup {
            Some(backup) => match paths::sha256_file(backup) {
                // Already holding the saved bytes: do not touch a file that may
                // be in use.
                Ok(want) if paths::sha256_file(&saved.path).as_ref() == Ok(&want) => Ok(()),
                Ok(_) => put(backup, &saved.path),
                Err(e) => Err(e),
            },
            None if saved.path.exists() => {
                fs::remove_file(&saved.path).map_err(|e| format!("{}: {e}", saved.path.display()))
            }
            None => Ok(()),
        };
        if let Err(e) = result {
            problems.push(e);
        }
    }
    if !problems.is_empty() {
        return Err(format!(
            "RECOVERY REQUIRED: the previous state was not fully restored ({}). \
             Fix the cause and run: eos recover. The write guard stays closed until then.",
            problems.join("; ")
        ));
    }
    fs::remove_file(journal_path()).map_err(|e| format!("cannot clear the journal: {e}"))?;
    let _ = fs::remove_dir_all(backup_dir());
    Ok(())
}

/// Replace `dst` with the bytes of `src` through a sibling temp file.
fn put(src: &Path, dst: &Path) -> Result<(), String> {
    let fail = |e: std::io::Error| format!("cannot write {}: {e}", dst.display());
    if let Some(parent) = dst.parent() {
        fs::create_dir_all(parent).map_err(fail)?;
    }
    if dst == launcher_path() {
        return install::publish_launcher(src);
    }
    // Never `<name>.js`: a host scans the plugin directory.
    let name = dst.file_name().unwrap_or_default().to_string_lossy().to_string();
    let tmp = dst.with_file_name(format!("{name}.eos-tmp"));
    let outcome = fs::copy(src, &tmp).and_then(|_| fs::rename(&tmp, dst));
    if outcome.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    outcome.map_err(fail)
}

struct PluginPlan {
    /// (installed file, canonical file of the version being activated)
    writes: Vec<(PathBuf, PathBuf)>,
    /// A second copy of a plugin the global scope already provides.
    removals: Vec<PathBuf>,
    scope: Option<Value>,
    manifest: Option<Value>,
}

fn config_root() -> Result<PathBuf, String> {
    if let Some(v) = std::env::var_os("OPENCODE_CONFIG_DIR") {
        return Ok(PathBuf::from(v));
    }
    // A redirected EOS_HOME is a test or a second profile. Writing the real
    // user's host config from it would be a silent cross-profile change.
    if paths::eos_home() != paths::home_dir().join(".eos") {
        return Err("EOS_HOME is redirected and has a global scope, but OPENCODE_CONFIG_DIR is not \
                    set; refusing to touch the default OpenCode configuration"
            .into());
    }
    Ok(paths::home_dir().join(".config").join("opencode"))
}

fn plugin_source(version_dir: &Path, name: &str) -> PathBuf {
    version_dir.join("engine").join("host-plugins").join("opencode").join(name)
}

fn optional_json(path: &Path) -> Result<Option<Value>, String> {
    if path.exists() { paths::read_json(path).map(Some) } else { Ok(None) }
}

/// Every byte sequence EOS is known to have written as a plugin. Ownership is
/// proven by content: a file EOS did not write is never replaced or removed.
fn known_plugin_hashes(cfg: &Config, extra: &[PathBuf]) -> HashSet<String> {
    let mut known = HashSet::new();
    let dirs = cfg.versions.iter().map(|v| PathBuf::from(&v.dir)).chain(extra.iter().cloned());
    for dir in dirs {
        for name in PLUGINS {
            if let Ok(hash) = paths::sha256_file(&plugin_source(&dir, name)) {
                known.insert(hash);
            }
        }
    }
    if let Ok(ledger) = paths::read_json::<Value>(&ledger_path()) {
        for hash in ledger["written"].as_array().into_iter().flatten() {
            known.extend(hash.as_str().map(str::to_string));
        }
    }
    known
}

/// `source_dir` is where the target version is now (staged or parked);
/// `final_dir` is where it will be once active.
fn plan_plugins(cfg: &Config, version: &str, source_dir: &Path, final_dir: &Path) -> Result<PluginPlan, String> {
    let mut plan = PluginPlan { writes: vec![], removals: vec![], scope: None, manifest: None };
    let known = known_plugin_hashes(cfg, &[source_dir.to_path_buf()]);
    let mut problems = Vec::new();
    let owned = |path: &Path| paths::sha256_file(path).map(|h| known.contains(&h)).unwrap_or(false);

    let scope_path = paths::eos_home().join("scope.json");
    let mut global_dir = None;
    if let Some(mut scope) = optional_json(&scope_path)? {
        let hosts = scope["hosts"].as_array().cloned().unwrap_or_default();
        if scope["mode"] == "global" && hosts.iter().any(|h| h == "opencode") {
            let root = config_root()?;
            global_dir = Some(root.join("plugins"));
            for name in PLUGINS {
                // The retired singular directory would load a second copy.
                let legacy = root.join("plugin").join(name);
                if legacy.exists() {
                    if owned(&legacy) {
                        plan.removals.push(legacy);
                    } else {
                        problems.push(format!("{} was not written by EOS", legacy.display()));
                    }
                }
            }
            let map = scope.as_object_mut().ok_or("scope.json is not an object")?;
            map.insert("version".into(), json!(version));
            map.insert("engine".into(), json!(final_dir.join("engine").display().to_string()));
            plan.scope = Some(scope);
        }
    }
    let mut targets: Vec<PathBuf> = Vec::new();
    if let Some(dir) = &global_dir {
        targets.extend(PLUGINS.iter().map(|n| dir.join(n)));
    }

    let manifest_path = paths::state_dir().join("plugin.manifest.json");
    if let Some(mut manifest) = optional_json(&manifest_path)? {
        let mut changed = false;
        if let Some(installed) = manifest["installed"].as_object_mut() {
            let mut gone = Vec::new();
            for (key, entry) in installed.iter_mut() {
                let path = PathBuf::from(key);
                let name = path.file_name().unwrap_or_default().to_string_lossy().to_string();
                let source = PathBuf::from(entry["source"].as_str().unwrap_or(""));
                // A copy that came from a workspace's own engine follows that
                // workspace, not the globally active version.
                if !path.is_file()
                    || !PLUGINS.contains(&name.as_str())
                    || !(source.starts_with(paths::versions_dir()) || source.starts_with(inactive_dir()))
                {
                    continue;
                }
                let is_global = targets.contains(&path);
                if !owned(&path) {
                    problems.push(format!("{} was changed after EOS wrote it", path.display()));
                } else if global_dir.is_some() && !is_global {
                    plan.removals.push(path);
                    gone.push(key.clone());
                    changed = true;
                    continue;
                } else if !is_global {
                    targets.push(path);
                }
                if let Some(map) = entry.as_object_mut() {
                    map.insert("source".into(), json!(plugin_source(final_dir, &name).display().to_string()));
                    changed = true;
                }
            }
            for key in gone {
                installed.remove(&key);
            }
        }
        if changed {
            plan.manifest = Some(manifest);
        }
    }

    for target in targets {
        let name = target.file_name().unwrap_or_default().to_string_lossy().to_string();
        let source = plugin_source(source_dir, &name);
        if !source.is_file() {
            problems.push(format!("version {version} does not ship {name}"));
        } else if target.exists() && !owned(&target) {
            problems.push(format!("{} was not written by EOS", target.display()));
        } else {
            plan.writes.push((target, source));
        }
    }
    if problems.is_empty() {
        return Ok(plan);
    }
    Err(format!(
        "host plugins cannot be kept in step with version {version}: {}. Nothing was changed. \
         Move the file(s) aside and retry.",
        problems.join("; ")
    ))
}

/// A path of `version` in its resting place: active, or parked when its name
/// would outrank the active version.
fn resting_place(name: &str, active: &str) -> PathBuf {
    if name > active { inactive_dir().join(name) } else { paths::versions_dir().join(name) }
}

#[cfg(test)]
thread_local! {
    pub static FAIL_AT: std::cell::RefCell<String> = const { std::cell::RefCell::new(String::new()) };
}

/// Test-only fault injection; compiled out of every shipped binary.
fn checkpoint(_name: &str) -> Result<(), String> {
    #[cfg(test)]
    if FAIL_AT.with(|f| *f.borrow() == _name) {
        return Err(format!("injected failure at {_name}"));
    }
    Ok(())
}

/// Make `version` active. `staged` is a fully built version directory that is
/// not visible under `versions/` yet; without it the version must be installed.
pub fn activate(_guard: &Guard, mut cfg: Config, version: &str, staged: Option<&Path>) -> Result<(), String> {
    if !valid_version(version) {
        return Err(format!("invalid version name: {version}"));
    }
    let final_dir = paths::versions_dir().join(version);
    let source_dir = match staged {
        Some(dir) => dir.to_path_buf(),
        None => cfg
            .versions
            .iter()
            .find(|v| v.version == version)
            .map(|v| PathBuf::from(&v.dir))
            .ok_or_else(|| format!("version {version} is not installed"))?,
    };
    if !source_dir.is_dir() {
        return Err(format!("version {version} is recorded but missing: {}", source_dir.display()));
    }
    verify_dir(&source_dir, version)?;
    let plugins = plan_plugins(&cfg, version, &source_dir, &final_dir)?;

    // Moves. Read the directory rather than the config: an unrecorded leftover
    // would be picked up by an old guard just the same.
    let mut moves: Vec<Move> = Vec::new();
    let replaced = inactive_dir().join(format!(".replaced-{}", uuid::Uuid::new_v4()));
    if staged.is_some() {
        for old in [final_dir.clone(), inactive_dir().join(version)] {
            if old.exists() {
                moves.push(Move { from: old, to: replaced.join(moves.len().to_string()) });
            }
        }
    }
    if source_dir != final_dir {
        moves.push(Move { from: source_dir.clone(), to: final_dir.clone() });
    }
    if paths::versions_dir().is_dir() {
        for entry in fs::read_dir(paths::versions_dir()).map_err(|e| e.to_string())?.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            let rest = resting_place(&name, version);
            if name != version && rest != entry.path() {
                moves.push(Move { from: entry.path(), to: rest });
            }
        }
    }
    for item in &moves {
        let vacated = moves.iter().any(|m| m.from == item.to);
        if item.to.exists() && !vacated {
            return Err(format!("{} already exists; nothing was changed", item.to.display()));
        }
    }

    // Config as it will be.
    for record in cfg.versions.iter_mut() {
        let moved = moves.iter().find(|m| m.from == Path::new(&record.dir) && !m.to.starts_with(&replaced));
        if record.version == version {
            record.dir = final_dir.display().to_string();
        } else if let Some(m) = moved {
            record.dir = m.to.display().to_string();
        }
    }
    cfg.active_version = Some(version.to_string());

    // Copy everything that will change, then publish the journal.
    let manifest_path = paths::state_dir().join("plugin.manifest.json");
    let scope_path = paths::eos_home().join("scope.json");
    let mut touched: Vec<PathBuf> = plugins.writes.iter().map(|w| w.0.clone()).collect();
    touched.extend(plugins.removals.iter().cloned());
    touched.extend([scope_path.clone(), manifest_path.clone(), ledger_path(), launcher_path(), paths::config_path()]);
    let _ = fs::remove_dir_all(backup_dir());
    fs::create_dir_all(backup_dir()).map_err(|e| format!("cannot create backups: {e}"))?;
    let mut files = Vec::new();
    for (index, path) in touched.into_iter().enumerate() {
        let backup = if path.exists() {
            let copy = backup_dir().join(format!("{index}.bak"));
            fs::copy(&path, &copy).map_err(|e| format!("cannot back up {}: {e}", path.display()))?;
            Some(copy)
        } else {
            None
        };
        files.push(SavedFile { path, backup });
    }
    let journal = Journal {
        schema: paths::SCHEMA,
        target: version.to_string(),
        started_at: paths::now_iso(),
        files,
        moves,
    };
    paths::write_json(&journal_path(), &journal)?;

    let outcome = apply(&cfg, version, &final_dir, &source_dir, &plugins, &journal, &scope_path, &manifest_path);
    if let Err(cause) = outcome {
        #[cfg(test)]
        if FAIL_AT.with(|f| f.borrow().starts_with("crash")) {
            return Err(cause);
        }
        return match restore(&journal) {
            Ok(()) => Err(format!("{cause}. The previous version was restored.")),
            Err(e) => Err(format!("{cause}. {e}")),
        };
    }
    fs::remove_file(journal_path()).map_err(|e| format!("cannot commit: {e}"))?;
    let _ = fs::remove_dir_all(backup_dir());
    let _ = fs::remove_dir_all(&replaced);
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn apply(
    cfg: &Config,
    version: &str,
    final_dir: &Path,
    source_dir: &Path,
    plugins: &PluginPlan,
    journal: &Journal,
    scope_path: &Path,
    manifest_path: &Path,
) -> Result<(), String> {
    for item in &journal.moves {
        if let Some(parent) = item.to.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        fs::rename(&item.from, &item.to).map_err(|e| {
            format!(
                "cannot move {} (is a session still running from it?): {e}",
                item.from.display()
            )
        })?;
        checkpoint("move")?;
        checkpoint("crash-move")?;
    }
    let mut written = Vec::new();
    for (target, source) in &plugins.writes {
        let source = final_dir.join(source.strip_prefix(source_dir).map_err(|e| e.to_string())?);
        put(&source, target)?;
        written.push(paths::sha256_file(target)?);
        checkpoint("plugin")?;
        checkpoint("crash-plugin")?;
    }
    for path in &plugins.removals {
        fs::remove_file(path).map_err(|e| format!("cannot remove {}: {e}", path.display()))?;
    }
    if let Some(scope) = &plugins.scope {
        paths::write_json(scope_path, scope)?;
    }
    if let Some(manifest) = &plugins.manifest {
        paths::write_json(manifest_path, manifest)?;
    }
    if !written.is_empty() {
        let mut ledger: Map<String, Value> = Map::new();
        let mut all: Vec<String> = paths::read_json::<Value>(&ledger_path())
            .ok()
            .and_then(|v| serde_json::from_value(v["written"].clone()).ok())
            .unwrap_or_default();
        all.extend(written);
        all.sort();
        all.dedup();
        ledger.insert("schema".into(), json!(paths::SCHEMA));
        ledger.insert("written".into(), json!(all));
        paths::write_json(&ledger_path(), &ledger)?;
    }
    checkpoint("launcher")?;
    let exe = final_dir.join(launcher_name());
    put(&exe, &launcher_path())?;
    checkpoint("config")?;
    paths::save_config(cfg)?;
    checkpoint("crash-config")?;

    // Verify what is on disk, not what was intended.
    verify_dir(final_dir, version)?;
    if paths::sha256_file(&exe)? != paths::sha256_file(&launcher_path())? {
        return Err("the launcher does not match the activated version".into());
    }
    for (target, source) in &plugins.writes {
        let source = final_dir.join(source.strip_prefix(source_dir).map_err(|e| e.to_string())?);
        if paths::sha256_file(&source)? != paths::sha256_file(target)? {
            return Err(format!("{} does not match the activated version", target.display()));
        }
    }
    let saved = strict_config()?;
    if saved.active_version.as_deref() != Some(version) {
        return Err("config.json does not name the activated version".into());
    }
    checkpoint("verify")
}

fn verify_dir(dir: &Path, version: &str) -> Result<(), String> {
    let (_, missing, changed) = install::verify_dir(dir, version)?;
    if missing.is_empty() && changed.is_empty() {
        return Ok(());
    }
    Err(format!(
        "version {version} is damaged: {} file(s) missing, {} changed (first: {})",
        missing.len(),
        changed.len(),
        missing.iter().chain(changed.iter()).next().map(String::as_str).unwrap_or("")
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::paths::VersionRecord;
    use std::collections::BTreeMap;
    use std::sync::{Mutex, MutexGuard};

    static ENV: Mutex<()> = Mutex::new(());

    struct Home {
        root: PathBuf,
        _env: MutexGuard<'static, ()>,
    }

    impl Drop for Home {
        fn drop(&mut self) {
            fail("");
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    fn fail(at: &str) {
        FAIL_AT.with(|f| *f.borrow_mut() = at.to_string());
    }

    fn write(path: &Path, text: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }

    fn build(dir: &Path, version: &str) -> VersionRecord {
        write(&dir.join("engine/tools/decision.py"), &format!("decision {version}"));
        for name in PLUGINS {
            write(&plugin_source(dir, name), &format!("{name} {version}"));
        }
        write(&dir.join(launcher_name()), &format!("launcher {version}"));
        let (engine, sha) = install::hash_relative(&dir.join("engine")).unwrap();
        let (entries, _) = install::hash_relative(dir).unwrap();
        let manifest = install::InstallManifest {
            schema: 1,
            version: version.into(),
            installed_at: String::new(),
            runtime_mode: "system".into(),
            engine_root_rel: "engine".into(),
            entries,
            manifest_sha256: String::new(),
        };
        paths::write_json(&dir.join("install-manifest.json"), &manifest).unwrap();
        VersionRecord {
            version: version.into(),
            dir: paths::versions_dir().join(version).display().to_string(),
            installed_at: String::new(),
            engine_files: engine.len(),
            engine_manifest_sha256: sha,
            runtime_mode: "system".into(),
        }
    }

    /// 1.0.2 installed the way 1.0.2 installed itself, with global plugins.
    fn home() -> Home {
        let env = ENV.lock().unwrap_or_else(|e| e.into_inner());
        let root = std::env::temp_dir().join(format!("eos-txn-{}", uuid::Uuid::new_v4()));
        std::env::set_var("EOS_HOME", root.join("home"));
        std::env::set_var("OPENCODE_CONFIG_DIR", root.join("host"));
        let record = build(&paths::versions_dir().join("1.0.2"), "1.0.2");
        let cfg = Config {
            schema: 1,
            active_version: Some("1.0.2".into()),
            previous_version: None,
            versions: vec![record],
            path_entry_added: false,
            path_entry: None,
        };
        paths::save_config(&cfg).unwrap();
        write(&launcher_path(), "launcher 1.0.2");
        write(
            &paths::eos_home().join("scope.json"),
            r#"{"schema":1,"mode":"global","root":"","hosts":["opencode"],"version":"1.0.2","note":"kept"}"#,
        );
        for name in PLUGINS {
            write(&root.join("host/plugins").join(name), &format!("{name} 1.0.2"));
        }
        fs::create_dir_all(paths::state_dir()).unwrap();
        Home { root, _env: env }
    }

    impl Home {
        fn plugin(&self, name: &str) -> PathBuf {
            self.root.join("host/plugins").join(name)
        }

        fn stage(&self, version: &str) -> Result<(), String> {
            let (guard, _) = begin()?;
            let mut cfg = strict_config()?;
            let staged = paths::eos_home().join("staging").join(version);
            let record = build(&staged, version);
            cfg.previous_version = cfg.active_version.clone();
            cfg.versions.retain(|v| v.version != version);
            cfg.versions.push(record);
            let out = activate(&guard, cfg, version, Some(&staged));
            let _ = fs::remove_dir_all(paths::eos_home().join("staging"));
            out
        }

        /// Everything a version switch may change, byte for byte.
        fn snapshot(&self) -> BTreeMap<String, String> {
            fn walk(dir: &Path, base: &Path, out: &mut BTreeMap<String, String>) {
                for entry in fs::read_dir(dir).unwrap().flatten() {
                    let path = entry.path();
                    if path.is_dir() {
                        walk(&path, base, out);
                    } else if !path.ends_with("activation.lock") {
                        let rel = path.strip_prefix(base).unwrap().display().to_string();
                        out.insert(rel, paths::sha256_file(&path).unwrap());
                    }
                }
            }
            let mut out = BTreeMap::new();
            walk(&self.root, &self.root, &mut out);
            out
        }

        fn assert_active(&self, version: &str) {
            let cfg = strict_config().unwrap();
            assert_eq!(cfg.active_version.as_deref(), Some(version));
            assert_eq!(fs::read_to_string(launcher_path()).unwrap(), format!("launcher {version}"));
            for name in PLUGINS {
                assert_eq!(fs::read_to_string(self.plugin(name)).unwrap(), format!("{name} {version}"));
            }
            // What a guard up to 1.0.2 would select.
            let greatest = fs::read_dir(paths::versions_dir())
                .unwrap()
                .flatten()
                .map(|e| e.file_name().to_string_lossy().to_string())
                .max();
            assert_eq!(greatest.as_deref(), Some(version));
            assert!(!journal_path().exists());
            let scope: Value = paths::read_json(&paths::eos_home().join("scope.json")).unwrap();
            assert_eq!(scope["version"], version);
            assert_eq!(scope["note"], "kept");
        }
    }

    #[test]
    fn update_and_rollback_move_every_part_together() {
        let h = home();
        h.stage("1.0.3").unwrap();
        h.assert_active("1.0.3");
        assert_eq!(install::rollback().unwrap(), "1.0.2");
        h.assert_active("1.0.2");
        assert!(inactive_dir().join("1.0.3").is_dir());
        assert_eq!(install::rollback().unwrap(), "1.0.3");
        h.assert_active("1.0.3");
    }

    #[test]
    fn version_1_0_10_is_not_outranked_by_1_0_2() {
        let h = home();
        h.stage("1.0.10").unwrap();
        h.assert_active("1.0.10");
        assert_eq!(install::rollback().unwrap(), "1.0.2");
        h.assert_active("1.0.2");
    }

    #[test]
    fn a_failed_update_restores_every_byte() {
        for at in ["move", "plugin", "launcher", "config", "verify"] {
            let h = home();
            let before = h.snapshot();
            fail(at);
            let error = h.stage("1.0.3").unwrap_err();
            fail("");
            assert!(error.contains("previous version was restored"), "{at}: {error}");
            assert_eq!(h.snapshot(), before, "{at}");
        }
    }

    #[test]
    fn a_failed_rollback_restores_every_byte() {
        for at in ["move", "plugin", "launcher", "config", "verify"] {
            let h = home();
            h.stage("1.0.3").unwrap();
            let before = h.snapshot();
            fail(at);
            install::rollback().unwrap_err();
            fail("");
            assert_eq!(h.snapshot(), before, "{at}");
        }
    }

    #[test]
    fn an_interrupted_change_is_journaled_and_recovered() {
        for at in ["crash-move", "crash-plugin", "crash-config"] {
            let h = home();
            h.stage("1.0.3").unwrap();
            let before = h.snapshot();
            fail(at);
            install::rollback().unwrap_err();
            fail("");
            assert!(journal_path().exists(), "{at}: the guard must see the journal");
            assert!(install::recover().unwrap());
            assert_eq!(h.snapshot(), before, "{at}");
        }
    }

    #[test]
    fn a_plugin_eos_did_not_write_blocks_and_is_untouched() {
        let h = home();
        write(&h.plugin("eos-guard.js"), "user version");
        let before = h.snapshot();
        let error = h.stage("1.0.3").unwrap_err();
        assert!(error.contains("eos-guard.js") && error.contains("Nothing was changed"), "{error}");
        assert_eq!(h.snapshot(), before);
    }

    #[test]
    fn a_missing_guard_is_completed_and_a_legacy_copy_is_retired() {
        let h = home();
        fs::remove_file(h.plugin("eos-guard.js")).unwrap();
        let legacy = h.root.join("host/plugin/eos-guard.js");
        write(&legacy, "eos-guard.js 1.0.2");
        h.stage("1.0.3").unwrap();
        h.assert_active("1.0.3");
        assert!(!legacy.exists());
    }

    #[test]
    fn project_copies_follow_their_own_engine() {
        let h = home();
        let dup = h.root.join("p1/.opencode/plugins/eos-guard.js");
        let own = h.root.join("p2/.opencode/plugins/eos-guard.js");
        write(&dup, "eos-guard.js 1.0.2");
        write(&own, "eos-guard.js 1.0.2");
        let source = plugin_source(&paths::versions_dir().join("1.0.2"), "eos-guard.js");
        let mut installed = Map::new();
        installed.insert(dup.display().to_string(), json!({"source": source}));
        installed.insert(own.display().to_string(), json!({"source": h.root.join("p2/.agents/x/eos-guard.js")}));
        paths::write_json(&paths::state_dir().join("plugin.manifest.json"), &json!({"schema":1,"installed":installed})).unwrap();
        h.stage("1.0.3").unwrap();
        assert!(!dup.exists(), "the global guard already covers this project");
        assert_eq!(fs::read_to_string(&own).unwrap(), "eos-guard.js 1.0.2");
    }

    #[test]
    fn without_a_global_scope_no_host_file_is_written() {
        let h = home();
        fs::remove_file(paths::eos_home().join("scope.json")).unwrap();
        h.stage("1.0.3").unwrap();
        assert_eq!(fs::read_to_string(h.plugin("eos-guard.js")).unwrap(), "eos-guard.js 1.0.2");
    }

    #[test]
    fn a_redirected_home_never_touches_the_default_host_config() {
        let h = home();
        let before = h.snapshot();
        std::env::remove_var("OPENCODE_CONFIG_DIR");
        let error = h.stage("1.0.3").unwrap_err();
        assert!(error.contains("OPENCODE_CONFIG_DIR"), "{error}");
        assert_eq!(h.snapshot(), before);
    }

    #[test]
    fn corrupt_config_and_damaged_target_change_nothing() {
        let h = home();
        h.stage("1.0.3").unwrap();
        write(&paths::versions_dir().join("1.0.2/engine/tools/decision.py"), "tampered");
        let before = h.snapshot();
        assert!(install::rollback().unwrap_err().contains("damaged"));
        assert_eq!(h.snapshot(), before);
        write(&paths::config_path(), "{ broken");
        let before = h.snapshot();
        assert!(install::rollback().is_err());
        assert!(h.stage("1.0.4").is_err());
        assert_eq!(h.snapshot(), before);
    }

    #[test]
    fn a_selection_made_by_an_old_launcher_is_completed_by_recover() {
        let h = home();
        h.stage("1.0.3").unwrap();
        install::rollback().unwrap();
        // 1.0.2's own rollback: config and launcher only.
        let mut cfg = strict_config().unwrap();
        cfg.active_version = Some("1.0.3".into());
        cfg.previous_version = Some("1.0.2".into());
        paths::save_config(&cfg).unwrap();
        assert!(paths::resolve_engine().unwrap_err().contains("eos recover"));
        assert!(install::recover().unwrap());
        h.assert_active("1.0.3");
    }

    #[test]
    fn a_second_version_change_is_refused_while_one_runs() {
        let _h = home();
        let _held = begin().unwrap();
        assert!(begin().err().unwrap().contains("another EOS version change"));
    }
}
