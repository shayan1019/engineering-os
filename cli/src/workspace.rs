//! Project identity and the workspace registry.
//!
//! Two separate things, deliberately:
//!
//! * **Project identity** lives *inside* the project, at `.eos/project.json`.
//!   It holds a UUID. That is what makes relocation lossless: moving the folder
//!   changes a path, never an identity.
//! * **Workspace registry** lives in user data (`%EOS_HOME%/workspaces.json`) and
//!   is only a convenience index for `eos workspace list`. Deleting it loses no
//!   project knowledge.
//!
//! The shipped `PROJECT_PROFILES.json` must therefore never be the only place a
//! project path exists: an installed EOS resolves projects from the registry and
//! from `.eos/project.json`, and treats the bundled profiles as hints only.

use crate::paths;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ProjectConfig {
    pub schema: u32,
    pub id: String,
    pub name: String,
    pub created_at: String,
    #[serde(default)]
    pub hosts: Vec<String>,
    #[serde(default)]
    pub eos_version: Option<String>,
    #[serde(default)]
    pub notes: Option<String>,
}

pub fn project_dir(project: &Path) -> PathBuf {
    project.join(".eos")
}

pub fn project_config_path(project: &Path) -> PathBuf {
    project_dir(project).join("project.json")
}

pub fn read_project(project: &Path) -> Option<ProjectConfig> {
    paths::read_json(&project_config_path(project)).ok()
}

/// Walk up from `start` looking for `.eos/project.json`.
pub fn discover(start: &Path) -> Option<(PathBuf, ProjectConfig)> {
    let start = if start.is_absolute() {
        start.to_path_buf()
    } else {
        std::env::current_dir().ok()?.join(start)
    };
    let start = fs::canonicalize(&start).unwrap_or(start);
    for ancestor in start.ancestors() {
        if let Some(cfg) = read_project(ancestor) {
            return Some((ancestor.to_path_buf(), cfg));
        }
    }
    None
}

/// Create or return the identity for a project directory.
pub fn ensure(project: &Path, name: Option<&str>, version: &str) -> Result<(ProjectConfig, bool), String> {
    let project = fs::canonicalize(project).unwrap_or_else(|_| project.to_path_buf());
    if !project.is_dir() {
        return Err(format!("not a directory: {}", project.display()));
    }
    if let Some(existing) = read_project(&project) {
        return Ok((existing, false));
    }
    let name = name
        .map(|s| s.to_string())
        .or_else(|| {
            project
                .file_name()
                .map(|s| s.to_string_lossy().to_string())
        })
        .unwrap_or_else(|| "project".into());
    let cfg = ProjectConfig {
        schema: paths::SCHEMA,
        id: uuid::Uuid::new_v4().to_string(),
        name,
        created_at: paths::now_iso(),
        hosts: Vec::new(),
        eos_version: Some(version.to_string()),
        notes: None,
    };
    paths::write_json(&project_config_path(&project), &cfg)?;
    Ok((cfg, true))
}

pub fn bind_host(project: &Path, host_id: &str) -> Result<(), String> {
    let path = project_config_path(project);
    let mut cfg: ProjectConfig = paths::read_json(&path)?;
    if !cfg.hosts.iter().any(|h| h == host_id) {
        cfg.hosts.push(host_id.to_string());
        paths::write_json(&path, &cfg)?;
    }
    Ok(())
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct WorkspaceEntry {
    pub id: String,
    pub path: String,
    pub name: String,
    pub added_at: String,
    #[serde(default)]
    pub project_id: String,
    #[serde(default)]
    pub last_open: Option<String>,
    #[serde(default)]
    pub missing: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Registry {
    pub schema: u32,
    pub workspaces: Vec<WorkspaceEntry>,
}

impl Default for Registry {
    fn default() -> Self {
        Registry {
            schema: paths::SCHEMA,
            workspaces: Vec::new(),
        }
    }
}

pub fn load_registry() -> Registry {
    let mut r: Registry = paths::read_json_or_default(&paths::workspaces_path());
    if r.schema == 0 {
        r.schema = paths::SCHEMA;
    }
    r
}

pub fn save_registry(r: &Registry) -> Result<(), String> {
    paths::write_json(&paths::workspaces_path(), r)
}

fn normalize(p: &Path) -> PathBuf {
    let abs = if p.is_absolute() {
        p.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(p)
    };
    // canonicalize resolves `..`, case and short names where the path exists.
    // `clean` strips the `\\?\` verbatim prefix Windows adds, so stored paths stay
    // comparable with paths typed by a user or reported by another tool.
    paths::clean(&fs::canonicalize(&abs).unwrap_or(abs))
}

pub fn add(path: &Path, name: Option<&str>, version: &str) -> Result<(WorkspaceEntry, bool), String> {
    let abs = normalize(path);
    if !abs.is_dir() {
        return Err(format!("not a directory: {}", abs.display()));
    }
    let (cfg, created) = ensure(&abs, name, version)?;
    let mut reg = load_registry();
    let key = abs.to_string_lossy().to_lowercase();
    if let Some(existing) = reg
        .workspaces
        .iter_mut()
        .find(|w| w.path.to_lowercase() == key || w.project_id == cfg.id)
    {
        existing.path = abs.display().to_string();
        existing.missing = false;
        let entry = existing.clone();
        save_registry(&reg)?;
        return Ok((entry, created));
    }
    let entry = WorkspaceEntry {
        id: uuid::Uuid::new_v4().to_string(),
        path: abs.display().to_string(),
        name: cfg.name.clone(),
        added_at: paths::now_iso(),
        project_id: cfg.id.clone(),
        last_open: None,
        missing: false,
    };
    reg.workspaces.push(entry.clone());
    save_registry(&reg)?;
    Ok((entry, created))
}

pub fn remove(id_or_path: &str) -> Result<WorkspaceEntry, String> {
    let mut reg = load_registry();
    let key = id_or_path.to_lowercase();
    let idx = reg
        .workspaces
        .iter()
        .position(|w| w.id.to_lowercase() == key || w.path.to_lowercase() == key)
        .or_else(|| {
            let n = normalize(Path::new(id_or_path));
            let n = n.to_string_lossy().to_lowercase();
            reg.workspaces.iter().position(|w| w.path.to_lowercase() == n)
        })
        .ok_or_else(|| format!("no registered workspace matches {id_or_path}"))?;
    let entry = reg.workspaces.remove(idx);
    save_registry(&reg)?;
    Ok(entry)
}

pub fn relocate(id: &str, new_path: &Path) -> Result<(WorkspaceEntry, ProjectConfig), String> {
    let abs = normalize(new_path);
    if !abs.is_dir() {
        return Err(format!("not a directory: {}", abs.display()));
    }
    let mut reg = load_registry();
    let key = id.to_lowercase();
    let idx = reg
        .workspaces
        .iter()
        .position(|w| w.id.to_lowercase() == key || w.path.to_lowercase() == key)
        .ok_or_else(|| format!("no registered workspace matches {id}"))?;
    // The identity must travel with the files. If the moved project carries the
    // same UUID we keep it; if it carries none we stamp the old id; if it carries
    // a *different* UUID that is a genuine conflict and we refuse.
    let cfg = match read_project(&abs) {
        Some(cfg) if cfg.id == reg.workspaces[idx].project_id => cfg,
        Some(cfg) => {
            return Err(format!(
                "refusing: {} already carries project id {} but workspace {} has id {}. \
                 Remove the stale registry entry or drop --force.",
                abs.display(),
                cfg.id,
                reg.workspaces[idx].id,
                reg.workspaces[idx].project_id
            ))
        }
        None => {
            let cfg = ProjectConfig {
                schema: paths::SCHEMA,
                id: reg.workspaces[idx].project_id.clone(),
                name: reg.workspaces[idx].name.clone(),
                created_at: reg.workspaces[idx].added_at.clone(),
                hosts: Vec::new(),
                eos_version: None,
                notes: Some(format!("relocated from {}", reg.workspaces[idx].path)),
            };
            paths::write_json(&project_config_path(&abs), &cfg)?;
            cfg
        }
    };
    reg.workspaces[idx].path = abs.display().to_string();
    reg.workspaces[idx].missing = false;
    let entry = reg.workspaces[idx].clone();
    save_registry(&reg)?;
    Ok((entry, cfg))
}

pub fn mark_missing() -> usize {
    let mut reg = load_registry();
    let mut n = 0usize;
    for w in reg.workspaces.iter_mut() {
        let missing = !Path::new(&w.path).is_dir();
        if missing {
            n += 1;
        }
        w.missing = missing;
    }
    let _ = save_registry(&reg);
    n
}

pub fn touch_open(id: &str) -> Result<(), String> {
    let mut reg = load_registry();
    if let Some(w) = reg.workspaces.iter_mut().find(|w| w.id == id) {
        w.last_open = Some(paths::now_iso());
        save_registry(&reg)?;
    }
    Ok(())
}

/// Resolve `--project` style selectors: a registry id, a registry path, or a
/// filesystem path. Returns the project root plus its identity.
pub fn resolve_selector(selector: &str) -> Result<(PathBuf, Option<ProjectConfig>), String> {
    let reg = load_registry();
    let key = selector.to_lowercase();
    if let Some(w) = reg
        .workspaces
        .iter()
        .find(|w| w.id.to_lowercase() == key || w.name.to_lowercase() == key)
    {
        let path = PathBuf::from(&w.path);
        return Ok((path.clone(), read_project(&path)));
    }
    let abs = normalize(Path::new(selector));
    if abs.is_dir() {
        return Ok((abs.clone(), read_project(&abs)));
    }
    if let Some(found) = discover(&abs) {
        return Ok((found.0, Some(found.1)));
    }
    Err(format!("cannot resolve project '{selector}'"))
}
