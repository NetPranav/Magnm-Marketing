//! In-app updates for the desktop app: what is allowed to be downloaded, how a package is verified, and the
//! commands the Settings > Updates screen calls.
//!
//! The Tauri updater plugin installs AppImage, deb, rpm, msi, nsis and app bundles. It cannot install a pacman
//! package, so an Arch install (marked by a file our package installs) downloads the signed `.pkg.tar.zst` named in
//! `latest.json` and installs it with `pkexec pacman -U`. Nothing is ever installed without a click.

use std::ffi::OsString;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine as _;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager, State, Url};
use tauri_plugin_updater::{Update, UpdaterExt};

use crate::engine::EngineSupervisor;

/// The file our pacman package installs. It holds the single word `pacman`.
pub const INSTALL_KIND_MARKER: &str = "/usr/share/magnm-studio/install-kind";
/// A quiet check runs at most once in this many seconds.
pub const CHECK_INTERVAL_SECS: u64 = 24 * 60 * 60;

const RELEASE_HOST: &str = "github.com";
const RELEASE_PREFIX: &str = "/NetPranav/Magnm-Marketing/releases/download/";
const ASSET_HOSTS: [&str; 2] = ["release-assets.githubusercontent.com", "objects.githubusercontent.com"];
const ASSET_PATH_PREFIX: &str = "/github-production-release-asset";
const MAX_REDIRECTS: usize = 5;
const MAX_SIGNATURE_CHARS: usize = 4096;

/// How this copy of the app was installed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum InstallKind {
    /// Installed from our Arch package: updates go through pacman.
    Pacman,
    /// Anything else: the updater plugin installs the update itself.
    Standard,
}

/// Decide the install kind from the text of the marker file (`None` when the file does not exist).
pub fn parse_install_kind(marker_text: Option<&str>) -> InstallKind {
    match marker_text.map(str::trim) {
        Some("pacman") => InstallKind::Pacman,
        _ => InstallKind::Standard,
    }
}

/// Read the marker file at `marker` (at most 64 bytes) and decide the install kind.
pub fn detect_install_kind(marker: &Path) -> InstallKind {
    let Ok(file) = std::fs::File::open(marker) else {
        return InstallKind::Standard;
    };
    let mut buf = Vec::new();
    // One byte more than the limit, so a huge file is told apart from a short one.
    if file.take(65).read_to_end(&mut buf).is_err() || buf.len() > 64 {
        return InstallKind::Standard;
    }
    match std::str::from_utf8(&buf) {
        Ok(text) => parse_install_kind(Some(text)),
        Err(_) => InstallKind::Standard,
    }
}

fn plain_host(url: &Url) -> bool {
    url.scheme() == "https"
        && url.username().is_empty()
        && url.password().is_none()
        && url.port().is_none()
        && url.fragment().is_none()
}

fn is_version_tag(tag: &str) -> bool {
    let Some(version) = tag.strip_prefix('v') else {
        return false;
    };
    is_plain_version(version)
}

fn is_plain_version(version: &str) -> bool {
    let parts: Vec<&str> = version.split('.').collect();
    parts.len() == 3 && parts.iter().all(|p| !p.is_empty() && p.len() <= 5 && p.bytes().all(|b| b.is_ascii_digit()))
}

fn is_file_name_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'.' || b == b'_' || b == b'-'
}

/// True for a release asset link we may download from: https, github.com, our repository's releases,
/// `/releases/download/vX.Y.Z/<file>`, no userinfo, no port, no query, no encoded slashes.
pub fn is_release_download_url(url: &Url) -> bool {
    if !plain_host(url) || url.host_str() != Some(RELEASE_HOST) || url.query().is_some() {
        return false;
    }
    let path = url.path();
    let lower = path.to_ascii_lowercase();
    if lower.contains("%2f") || lower.contains("%5c") || lower.contains("%2e") || path.contains('\\') {
        return false;
    }
    let Some(rest) = path.strip_prefix(RELEASE_PREFIX) else {
        return false;
    };
    let mut parts = rest.split('/');
    let (Some(tag), Some(file), None) = (parts.next(), parts.next(), parts.next()) else {
        return false;
    };
    is_version_tag(tag)
        && !file.is_empty()
        && !file.starts_with('.')
        && !file.starts_with('-')
        && file.bytes().all(is_file_name_char)
}

/// True for a URL a download may be redirected to: a release asset link, or the GitHub release-asset host.
pub fn is_allowed_redirect(url: &Url) -> bool {
    if is_release_download_url(url) {
        return true;
    }
    if !plain_host(url) {
        return false;
    }
    let Some(host) = url.host_str() else {
        return false;
    };
    ASSET_HOSTS.contains(&host)
        && url.path().starts_with(ASSET_PATH_PREFIX)
        && !url.path().to_ascii_lowercase().contains("%2e")
        && !url.path().contains("//")
}

/// The Arch package a release offers, as named in `latest.json`'s `extra.pacman` field.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArchPackage {
    pub url: Url,
    pub signature: String,
    pub file_name: String,
}

/// True for the file name of our Arch package of `version`: `magnm-studio-X.Y.Z-N-x86_64.pkg.tar.zst`.
fn is_arch_package_name(name: &str, version: &str) -> bool {
    if !is_plain_version(version) {
        return false;
    }
    let Some(rest) = name.strip_prefix("magnm-studio-").and_then(|r| r.strip_prefix(version)) else {
        return false;
    };
    let Some(rel) = rest.strip_prefix('-').and_then(|r| r.strip_suffix("-x86_64.pkg.tar.zst")) else {
        return false;
    };
    !rel.is_empty() && rel.len() <= 3 && rel.bytes().all(|b| b.is_ascii_digit())
}

fn looks_like_base64(text: &str) -> bool {
    !text.is_empty()
        && text.len() <= MAX_SIGNATURE_CHARS
        && text.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'+' || b == b'/' || b == b'=')
}

/// Read and check `extra.pacman` of a `latest.json` for `version`.
pub fn arch_package_from_manifest(raw: &Value, version: &str) -> Result<ArchPackage, String> {
    let none = "This release has no Arch package, so it cannot be installed here yet.";
    let entry = raw.get("extra").and_then(|e| e.get("pacman")).and_then(Value::as_object).ok_or(none)?;
    let url_text = entry.get("url").and_then(Value::as_str).ok_or(none)?;
    let signature = entry.get("signature").and_then(Value::as_str).ok_or(none)?;
    let wrong = "The Arch package listed for this release is not one of ours, so it was not downloaded.";
    let url = Url::parse(url_text).map_err(|_| wrong.to_string())?;
    if !is_release_download_url(&url) || !looks_like_base64(signature) {
        return Err(wrong.to_string());
    }
    let mut segments = url.path().trim_start_matches('/').rsplit('/');
    let file_name = segments.next().unwrap_or_default().to_string();
    let tag = segments.next().unwrap_or_default();
    if tag != format!("v{version}") || !is_arch_package_name(&file_name, version) {
        return Err(wrong.to_string());
    }
    Ok(ArchPackage { url, signature: signature.to_string(), file_name })
}

fn base64_text(value: &str) -> Result<String, String> {
    let bytes = base64::engine::general_purpose::STANDARD.decode(value.trim()).map_err(|_| "not base64".to_string())?;
    String::from_utf8(bytes).map_err(|_| "not text".to_string())
}

/// Verify `bytes` against a minisign signature (the `.sig` file text, base64) with the updater public key from
/// `tauri.conf.json` (also base64). The signature must be bound to `version`.
pub fn verify_minisign(bytes: &[u8], signature_b64: &str, pubkey_b64: &str, version: &str) -> Result<(), String> {
    let bad_sig = "The update's signature is not valid, so it was not installed.";
    let key_text = base64_text(pubkey_b64).map_err(|_| "This build has no valid update key, so updates are off.".to_string())?;
    let key = minisign_verify::PublicKey::decode(&key_text)
        .map_err(|_| "This build has no valid update key, so updates are off.".to_string())?;
    let sig_text = base64_text(signature_b64).map_err(|_| bad_sig.to_string())?;
    let signature = minisign_verify::Signature::decode(&sig_text).map_err(|_| bad_sig.to_string())?;
    key.verify(bytes, &signature, true).map_err(|_| bad_sig.to_string())?;
    // Only after the signature checked out is its trusted comment worth reading.
    let signed_version = signature.trusted_comment().split('\t').find_map(|field| field.strip_prefix("version:"));
    match signed_version {
        Some(v) if v.trim_start_matches('v') == version => Ok(()),
        Some(_) => Err("The update was signed for a different version than it announces, so it was not installed.".to_string()),
        None => Err("The update's signature is not tied to a version, so it was not installed.".to_string()),
    }
}

/// True when a quiet update check is due: never checked, or the last one was a day or more ago (or in the future).
pub fn should_check_now(now: u64, last_checked: Option<u64>) -> bool {
    match last_checked {
        None => true,
        Some(last) if last > now => true,
        Some(last) => now - last >= CHECK_INTERVAL_SECS,
    }
}

/// The update a check found.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UpdateInfo {
    pub version: String,
    pub notes: String,
    pub date: Option<String>,
}

/// What the quiet daily check remembers between runs.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CheckCache {
    pub checked_at: u64,
    pub available: Option<UpdateInfo>,
}

/// Read the remembered check result; a missing or damaged file gives `None`.
pub fn read_check_cache(path: &Path) -> Option<CheckCache> {
    let file = std::fs::File::open(path).ok()?;
    let mut buf = Vec::new();
    file.take(64 * 1024 + 1).read_to_end(&mut buf).ok()?;
    if buf.len() > 64 * 1024 {
        return None;
    }
    serde_json::from_slice(&buf).ok()
}

/// Remember a check result (best effort).
pub fn write_check_cache(path: &Path, cache: &CheckCache) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let text = serde_json::to_string(cache).map_err(std::io::Error::other)?;
    std::fs::write(path, text)
}

/// After pacman replaces the program file, Linux reports the old path with " (deleted)" on the end. Remove it.
pub fn clean_exe_path(exe: &Path) -> PathBuf {
    let text = exe.to_string_lossy();
    match text.strip_suffix(" (deleted)") {
        Some(clean) => PathBuf::from(clean),
        None => exe.to_path_buf(),
    }
}

/// The arguments for `pkexec` that install one downloaded package. The path must be absolute and named like our
/// package, so nothing else can be passed to pacman.
pub fn pacman_install_args(package: &Path) -> Result<Vec<OsString>, String> {
    let name = package.file_name().and_then(|n| n.to_str()).unwrap_or_default();
    let version = name.strip_prefix("magnm-studio-").and_then(|r| r.split('-').next()).unwrap_or_default();
    if !package.is_absolute() || !is_arch_package_name(name, version) {
        return Err("That file is not an Arch package of Magnm Studio, so it was not installed.".to_string());
    }
    Ok(vec![
        OsString::from("pacman"),
        OsString::from("-U"),
        OsString::from("--noconfirm"),
        OsString::from("--"),
        package.as_os_str().to_os_string(),
    ])
}

/// A plain sentence for a `pkexec` exit code.
pub fn describe_pkexec_failure(code: Option<i32>) -> String {
    match code {
        Some(126) => "The password prompt was cancelled, so the update was not installed.".to_string(),
        Some(127) => "Your system could not ask for the administrator password, so the update was not installed. Update with pacman in a terminal instead.".to_string(),
        _ => "Arch's package manager could not install the update. Nothing was changed; try again or update with pacman in a terminal.".to_string(),
    }
}

/// A plain sentence for an updater plugin error. The details go to the log, never to the screen.
pub fn plain_updater_error(err: &tauri_plugin_updater::Error) -> String {
    eprintln!("[magnm-updater] {err}");
    use tauri_plugin_updater::Error as E;
    match err {
        E::Reqwest(_) | E::Network(_) | E::ReleaseNotFound => {
            "Could not reach the update server. Check your internet connection and try again.".to_string()
        }
        E::Minisign(_) | E::Base64(_) | E::SignatureUtf8(_) | E::SignedVersionMismatch { .. } | E::MissingSignedVersion => {
            "The update's signature did not match, so it was not installed.".to_string()
        }
        E::TargetNotFound(_) | E::TargetsNotFound(_) => {
            "This release has no update for the way Magnm Studio is installed here yet.".to_string()
        }
        E::AuthenticationFailed => "The update needs your administrator password and it was not given.".to_string(),
        _ => "Could not complete the update. Nothing was changed; try again later.".to_string(),
    }
}

// ---- commands ----

/// What the Settings screen needs to draw itself, without any network call.
#[derive(Debug, Clone, Serialize)]
pub struct UpdateStatus {
    pub current_version: String,
    pub install_kind: InstallKind,
    /// False while this build has no real update key: checking is then off.
    pub configured: bool,
    pub available: Option<UpdateInfo>,
    pub last_checked: Option<u64>,
}

/// The result of installing.
#[derive(Debug, Clone, Serialize)]
pub struct InstallOutcome {
    pub version: String,
    pub restart_needed: bool,
}

/// The update found by the last check, kept until it is installed or a newer check replaces it.
#[derive(Default)]
pub struct UpdateState {
    pending: Mutex<Option<Update>>,
    info: Mutex<Option<UpdateInfo>>,
    busy: AtomicBool,
}

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn cache_path(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_data_dir().ok().map(|d| d.join("update-check.json"))
}

fn configured_pubkey(app: &AppHandle) -> Option<String> {
    const PLACEHOLDER: &str = "REPLACE_WITH_TAURI_UPDATER_PUBKEY";
    let key = app.config().plugins.0.get("updater")?.get("pubkey")?.as_str()?.trim().to_string();
    (!key.is_empty() && key != PLACEHOLDER).then_some(key)
}

/// Tell the Settings screen and the badge about progress or a new update.
fn emit(app: &AppHandle, event: &str, payload: impl Serialize + Clone) {
    let _ = app.emit(event, payload);
}

/// The updater, set up to follow redirects only to GitHub release hosts, and to stop the engine before Windows
/// runs its installer (the installer cannot replace a running engine).
fn release_updater(app: &AppHandle) -> Result<tauri_plugin_updater::Updater, String> {
    let handle = app.clone();
    app.updater_builder()
        .configure_client(|client| {
            client.connect_timeout(Duration::from_secs(20)).redirect(reqwest::redirect::Policy::custom(|attempt| {
                if attempt.previous().len() >= MAX_REDIRECTS {
                    attempt.error("too many redirects")
                } else if is_allowed_redirect(attempt.url()) {
                    attempt.follow()
                } else {
                    attempt.error("redirect target is not allowed")
                }
            }))
        })
        .on_before_exit(move || {
            if let Some(supervisor) = handle.try_state::<Arc<EngineSupervisor>>() {
                supervisor.stop();
            }
        })
        .build()
        .map_err(|err| plain_updater_error(&err))
}

fn install_kind() -> InstallKind {
    detect_install_kind(Path::new(INSTALL_KIND_MARKER))
}

fn update_info(update: &Update) -> UpdateInfo {
    UpdateInfo {
        version: update.version.clone(),
        notes: update.body.clone().unwrap_or_default(),
        date: update.date.map(|d| d.to_string()),
    }
}

/// Ask GitHub whether a newer version exists. Remembers the answer and the time.
async fn run_check(app: &AppHandle, state: &UpdateState) -> Result<Option<UpdateInfo>, String> {
    if !crate::is_updater_configured(app) {
        return Err("Updates are not set up in this build yet.".to_string());
    }
    let updater = release_updater(app)?;
    let found = updater.check().await.map_err(|err| plain_updater_error(&err))?;
    let info = match found {
        Some(update) => {
            if !is_release_download_url(&update.download_url) {
                return Err("The update points somewhere other than Magnm's releases, so it was ignored.".to_string());
            }
            if install_kind() == InstallKind::Pacman {
                arch_package_from_manifest(&update.raw_json, &update.version)?;
            }
            let info = update_info(&update);
            *state.pending.lock().map_err(|_| "Update state is unavailable.".to_string())? = Some(update);
            Some(info)
        }
        None => {
            *state.pending.lock().map_err(|_| "Update state is unavailable.".to_string())? = None;
            None
        }
    };
    *state.info.lock().map_err(|_| "Update state is unavailable.".to_string())? = info.clone();
    if let Some(path) = cache_path(app) {
        let _ = write_check_cache(&path, &CheckCache { checked_at: now_secs(), available: info.clone() });
    }
    if let Some(found) = &info {
        emit(app, "magnm:update-available", found.clone());
    }
    Ok(info)
}

/// What the screen needs to draw itself (no network).
#[tauri::command]
pub fn update_status(app: AppHandle, state: State<'_, UpdateState>) -> UpdateStatus {
    let cached = cache_path(&app).and_then(|p| read_check_cache(&p));
    let remembered = state.info.lock().ok().and_then(|g| g.clone());
    UpdateStatus {
        current_version: app.package_info().version.to_string(),
        install_kind: install_kind(),
        configured: crate::is_updater_configured(&app),
        available: remembered.or_else(|| cached.as_ref().and_then(|c| c.available.clone())),
        last_checked: cached.map(|c| c.checked_at),
    }
}

/// "Check for updates": ask GitHub now.
#[tauri::command]
pub async fn check_update(app: AppHandle, state: State<'_, UpdateState>) -> Result<Option<UpdateInfo>, String> {
    run_check(&app, &state).await
}

/// "Download and install": only ever called by a click. Shows progress through events and never restarts by itself.
#[tauri::command]
pub async fn install_update(app: AppHandle, state: State<'_, UpdateState>) -> Result<InstallOutcome, String> {
    if state.busy.swap(true, Ordering::SeqCst) {
        return Err("An update is already being installed.".to_string());
    }
    let result = install_inner(&app, &state).await;
    state.busy.store(false, Ordering::SeqCst);
    result
}

async fn install_inner(app: &AppHandle, state: &UpdateState) -> Result<InstallOutcome, String> {
    let mut pending = state.pending.lock().map_err(|_| "Update state is unavailable.".to_string())?.take();
    if pending.is_none() {
        run_check(app, state).await?;
        pending = state.pending.lock().map_err(|_| "Update state is unavailable.".to_string())?.take();
    }
    let Some(mut update) = pending else {
        return Err("Magnm Studio is already up to date.".to_string());
    };
    if !is_release_download_url(&update.download_url) {
        return Err("The update points somewhere other than Magnm's releases, so it was not downloaded.".to_string());
    }
    let version = update.version.clone();
    let progress_app = app.clone();
    let mut downloaded: u64 = 0;
    let on_chunk = move |chunk: usize, total: Option<u64>| {
        downloaded += chunk as u64;
        emit(&progress_app, "magnm:update-progress", serde_json::json!({"phase": "downloading", "downloaded": downloaded, "total": total}));
    };
    if install_kind() == InstallKind::Pacman {
        install_with_pacman(app, &mut update, on_chunk).await?;
    } else {
        emit(app, "magnm:update-progress", serde_json::json!({"phase": "downloading", "downloaded": 0, "total": null}));
        update.download_and_install(on_chunk, || {}).await.map_err(|err| plain_updater_error(&err))?;
    }
    *state.info.lock().map_err(|_| "Update state is unavailable.".to_string())? = None;
    if let Some(path) = cache_path(app) {
        let _ = write_check_cache(&path, &CheckCache { checked_at: now_secs(), available: None });
    }
    emit(app, "magnm:update-ready", version.clone());
    Ok(InstallOutcome { version, restart_needed: true })
}

/// Arch: download the package named in latest.json (verified by the plugin against the updater key), save it,
/// verify it again from disk, then install it with pkexec and pacman.
async fn install_with_pacman(
    app: &AppHandle,
    update: &mut Update,
    on_chunk: impl FnMut(usize, Option<u64>),
) -> Result<(), String> {
    let package = arch_package_from_manifest(&update.raw_json, &update.version)?;
    let pubkey = configured_pubkey(app).ok_or("Updates are not set up in this build yet.")?;
    let version = update.version.clone();
    update.download_url = package.url.clone();
    update.signature = package.signature.clone();
    let bytes = update.download(on_chunk, || {}).await.map_err(|err| plain_updater_error(&err))?;

    let dir = app.path().app_data_dir().map_err(|_| "The update folder is unavailable.".to_string())?.join("updates");
    let file = dir.join(&package.file_name);
    let signature = package.signature.clone();
    let result = tauri::async_runtime::spawn_blocking(move || -> Result<(), String> {
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).map_err(|_| "Could not save the update.".to_string())?;
        std::fs::write(&file, &bytes).map_err(|_| "Could not save the update.".to_string())?;
        // Check the file that pacman will read, not only the bytes that were downloaded.
        let on_disk = std::fs::read(&file).map_err(|_| "Could not read the saved update.".to_string())?;
        verify_minisign(&on_disk, &signature, &pubkey, &version)?;
        let args = pacman_install_args(&file)?;
        let status = std::process::Command::new("pkexec")
            .args(&args)
            .status()
            .map_err(|_| describe_pkexec_failure(Some(127)))?;
        let _ = std::fs::remove_dir_all(&dir);
        if status.success() { Ok(()) } else { Err(describe_pkexec_failure(status.code())) }
    })
    .await
    .map_err(|_| "The update was interrupted.".to_string())?;
    result
}

/// "Restart to finish": stop the engine and start the new program. Linux spawns the new copy a moment later so the
/// single-instance lock is free; other systems use Tauri's own restart.
#[tauri::command]
pub fn restart_app(app: AppHandle) {
    if let Some(supervisor) = app.try_state::<Arc<EngineSupervisor>>() {
        supervisor.stop();
    }
    #[cfg(target_os = "linux")]
    {
        if let Ok(exe) = std::env::current_exe() {
            let exe = clean_exe_path(&exe);
            let _ = std::process::Command::new("sh")
                .arg("-c")
                .arg("sleep 1; exec \"$0\" \"$@\"")
                .arg(exe)
                .args(std::env::args_os().skip(1))
                .spawn();
            app.exit(0);
            return;
        }
    }
    app.restart();
}

/// Check quietly once a day (and at start-up if a day has passed). It only tells the screen; it installs nothing.
pub fn start_background_checks(app: AppHandle) {
    std::thread::spawn(move || loop {
        let due = cache_path(&app)
            .map(|p| should_check_now(now_secs(), read_check_cache(&p).map(|c| c.checked_at)))
            .unwrap_or(false);
        if due {
            if let Some(state) = app.try_state::<UpdateState>() {
                if let Err(err) = tauri::async_runtime::block_on(run_check(&app, &state)) {
                    eprintln!("[magnm-updater] quiet check failed: {err}");
                }
            }
        }
        std::thread::sleep(Duration::from_secs(60 * 60));
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    // A throwaway key pair made only for these tests (its secret half was deleted). `GOOD_SIG` signs `PACKAGE`
    // and is bound to version 9.9.9.
    const PUBKEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEEyQkREMkMyQTA0NEE4RTAKUldUZ3FFU2d3dEs5b2dEeTFudlNFYytBa3NhL1ZUMklyRTdORFQ4ZWQ5VlB6RmRlRHkxVnVzQVUK";
    const OTHER_PUBKEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDM2MUVFRjkxODc4NTg5M0MKUldROGlZV0hrZThlTnAwUGY3bXoyL2dpTFB0QVpIYi9MWmNkOFBDTU1UWnpmUk5VSFRTYngrMWQK";
    const PACKAGE: &[u8] = b"magnm-test-package-bytes-v1";
    const GOOD_SIG: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVUZ3FFU2d3dEs5b3NURmNScmphYTR0cjFyMGJSZ0VjNU0rWnkzalE3TUF3K0dEU2ZoUFJlSGxmQlg5c1dPZDFBd1p5TXRKQnJ2a2lKb1RUR1BpbHBqa1pxczdtN0hqY2dvPQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDoxNzk1NTM2NjkwCWZpbGU6ZGF0YS5iaW4JdmVyc2lvbjo5LjkuOQordnYyekJjL3FxNzl6WHpJOUxtVWxQRmhDSm9HMWlyYXFWR2FtalpMLzJnTndxcG9JcUFucVFzOEJMRzd6MU9WSk0yMDZTQVE2M09NQ0VrVXZ0VGZCUT09Cg==";
    const OTHER_KEY_SIG: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVROGlZV0hrZThlTmpNb0d0S01FWDNRZHYxZGFCT1BZdlE1V1BhRy9kZitqcWRyV0o2cVFtRFFPM20xTm1KVXNEcHBHNGJBNFZla1R0Y2FmUVlpZ0I2QmZMZ0lVdDZhN2cwPQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDoxNzk1NTM2NjkwCWZpbGU6ZGF0YS5iaW4JdmVyc2lvbjo5LjkuOQp2TTRlL0J4YWF1cGtpTXJub2pIdmVuMGMyLy80aElETFhINFJ3aEo4eXJsdHV2QU9yeG9Cc0FJOHdHbDZ4SjBybllnQ3N3NHNoRVZzYVNXRDlwM1dBdz09Cg==";
    const NO_VERSION_SIG: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVUZ3FFU2d3dEs5b3FRVExORWI0WUZWUGVGUWxKL1k4S1lmMm9SZUNRTHJETE1zemVRZWdITERhWjJYUTNKdVNqdXB2aWk5RjRES01mSm9jU2hoR3d0Tnc4dFZNTFJydWdRPQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDoxNzk1NTM2Njg5CWZpbGU6ZF9ub3Zlci5iaW4KWWJGMUtvQmlWWmJyYytyQXlkUXNzZnBybW1pNVEvTlZnQjd0WWN1ZXpYZWdMSTFWdEZDZ0V2LzVUSVBhejV4eE5qTi9DaFM0ZFg5djJLUHJBZjBVQVE9PQo=";
    const OLD_VERSION_SIG: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVUZ3FFU2d3dEs5b3RPRTR3cFRlV1IzMFRsbVlpYzQ3NXJXQjJNck54VUQ2d3BONHdrcUtCdVFGellKSnVLOGM0RERQQ0hDSzJudTJOdzRPSzZvNjMydUVCK1lmdFhPeWdjPQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDoxNzk1NTM2Njg5CWZpbGU6ZF92ZXIxLmJpbgl2ZXJzaW9uOjEuMC4wCkZOQzEvQUkyRXY1eTh0OUIxaTdUOUxteWlNVnlZZE9GTWFIeWFSOG93WFVWNkhrbVN0b0FYT3JKV3JDaWM4c3g4bXhrREN6b2ZzNjJMVDFtUHNlNUR3PT0K";

    fn url(s: &str) -> Url {
        Url::parse(s).unwrap()
    }

    const GOOD: &str =
        "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0-1-x86_64.pkg.tar.zst";

    // ---- install kind ----

    #[test]
    fn marker_with_pacman_means_pacman() {
        assert_eq!(parse_install_kind(Some("pacman\n")), InstallKind::Pacman);
        assert_eq!(parse_install_kind(Some("  pacman ")), InstallKind::Pacman);
    }

    #[test]
    fn anything_else_is_a_standard_install() {
        assert_eq!(parse_install_kind(None), InstallKind::Standard);
        assert_eq!(parse_install_kind(Some("")), InstallKind::Standard);
        assert_eq!(parse_install_kind(Some("deb")), InstallKind::Standard);
        assert_eq!(parse_install_kind(Some("pacman extra")), InstallKind::Standard);
        assert_eq!(parse_install_kind(Some("PACMAN")), InstallKind::Standard);
        assert_eq!(parse_install_kind(Some("not pacman\npacman")), InstallKind::Standard);
    }

    #[test]
    fn detects_the_marker_file_on_disk() {
        let dir = tempdir("kind");
        let marker = dir.join("install-kind");
        assert_eq!(detect_install_kind(&marker), InstallKind::Standard);
        std::fs::write(&marker, "pacman\n").unwrap();
        assert_eq!(detect_install_kind(&marker), InstallKind::Pacman);
        std::fs::write(&marker, vec![b'a'; 100_000]).unwrap();
        assert_eq!(detect_install_kind(&marker), InstallKind::Standard);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    // ---- URL allowlist ----

    #[test]
    fn accepts_our_release_assets() {
        assert!(is_release_download_url(&url(GOOD)));
        assert!(is_release_download_url(&url(
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v1.20.3/latest.json"
        )));
    }

    #[test]
    fn refuses_hostile_release_urls() {
        let hostile = [
            "http://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a.zst",
            "https://github.com.evil.example/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a.zst",
            "https://evil.example/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a.zst",
            "https://github.com@evil.example/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a.zst",
            "https://user:pw@github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a.zst",
            "https://github.com:8443/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a.zst",
            "https://github.com./NetPranav/Magnm-Marketing/releases/download/v0.2.0/a.zst",
            "https://GITHUB.com.evil.example/x",
            "https://github.com/NetPranav/Other-Repo/releases/download/v0.2.0/a.zst",
            "https://github.com/Evil/Magnm-Marketing/releases/download/v0.2.0/a.zst",
            "https://github.com/NetPranav/Magnm-Marketing/archive/main.zip",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a/b.zst",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/latest/a.zst",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2/a.zst",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/../../../../Evil/x/a.zst",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/%2e%2e/a.zst",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a%2fb.zst",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a%5cb.zst",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a.zst?x=1",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a.zst#frag",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a b.zst",
            "ftp://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/a.zst",
            "file:///etc/passwd",
        ];
        for raw in hostile {
            if let Ok(parsed) = Url::parse(raw) {
                assert!(!is_release_download_url(&parsed), "should refuse {raw}");
            }
        }
    }

    #[test]
    fn redirects_may_go_to_github_release_hosts_only() {
        assert!(is_allowed_redirect(&url(GOOD)));
        assert!(is_allowed_redirect(&url(
            "https://release-assets.githubusercontent.com/github-production-release-asset/123/abc-def?sp=r&sig=x"
        )));
        assert!(is_allowed_redirect(&url(
            "https://objects.githubusercontent.com/github-production-release-asset-2e65be/123/abc?X-Amz-Signature=x"
        )));
        let hostile = [
            "http://release-assets.githubusercontent.com/github-production-release-asset/1/a",
            "https://evil.example/github-production-release-asset/1/a",
            "https://githubusercontent.com/github-production-release-asset/1/a",
            "https://raw.githubusercontent.com/NetPranav/Magnm-Marketing/main/x",
            "https://release-assets.githubusercontent.com.evil.example/github-production-release-asset/1/a",
            "https://release-assets.githubusercontent.com/other/1/a",
            "https://release-assets.githubusercontent.com:444/github-production-release-asset/1/a",
            "https://user@release-assets.githubusercontent.com/github-production-release-asset/1/a",
            "https://github.com/NetPranav/Magnm-Marketing/raw/main/x",
            "https://127.0.0.1/github-production-release-asset/1/a",
            "https://169.254.169.254/latest/meta-data",
        ];
        for raw in hostile {
            assert!(!is_allowed_redirect(&url(raw)), "should refuse {raw}");
        }
    }

    // ---- latest.json extra.pacman ----

    fn manifest(url: &str, sig: &str) -> Value {
        serde_json::json!({"version": "0.2.0", "extra": {"pacman": {"url": url, "signature": sig}}})
    }

    #[test]
    fn reads_the_arch_package_from_extra() {
        let pkg = arch_package_from_manifest(&manifest(GOOD, GOOD_SIG), "0.2.0").unwrap();
        assert_eq!(pkg.file_name, "magnm-studio-0.2.0-1-x86_64.pkg.tar.zst");
        assert_eq!(pkg.url.as_str(), GOOD);
        assert_eq!(pkg.signature, GOOD_SIG);
    }

    #[test]
    fn refuses_a_manifest_with_no_arch_package() {
        assert!(arch_package_from_manifest(&serde_json::json!({"version": "0.2.0"}), "0.2.0").is_err());
        assert!(arch_package_from_manifest(&serde_json::json!({"extra": {}}), "0.2.0").is_err());
        assert!(arch_package_from_manifest(&serde_json::json!({"extra": {"pacman": "x"}}), "0.2.0").is_err());
        assert!(arch_package_from_manifest(&manifest(GOOD, ""), "0.2.0").is_err());
        assert!(arch_package_from_manifest(&manifest(GOOD, "not base64 !!"), "0.2.0").is_err());
    }

    #[test]
    fn refuses_an_arch_package_from_the_wrong_place_or_version() {
        let bad = [
            "https://evil.example/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0-1-x86_64.pkg.tar.zst",
            "http://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0-1-x86_64.pkg.tar.zst",
            // another version's package under this version's tag, and the reverse
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.1.0-1-x86_64.pkg.tar.zst",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.1.0/magnm-studio-0.2.0-1-x86_64.pkg.tar.zst",
            // not our package name
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/other-0.2.0-1-x86_64.pkg.tar.zst",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0-1-x86_64.pkg.tar.zst.exe",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0-1-x86_64.AppImage",
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/-magnm-studio-0.2.0-1-x86_64.pkg.tar.zst",
        ];
        for raw in bad {
            assert!(arch_package_from_manifest(&manifest(raw, GOOD_SIG), "0.2.0").is_err(), "should refuse {raw}");
        }
        // an odd version string never matches a file name
        assert!(arch_package_from_manifest(&manifest(GOOD, GOOD_SIG), "0.2.0/../x").is_err());
        assert!(arch_package_from_manifest(&manifest(GOOD, GOOD_SIG), "").is_err());
    }

    // ---- signatures ----

    #[test]
    fn accepts_a_good_signature() {
        assert!(verify_minisign(PACKAGE, GOOD_SIG, PUBKEY, "9.9.9").is_ok());
    }

    #[test]
    fn refuses_changed_bytes() {
        let mut tampered = PACKAGE.to_vec();
        tampered[0] ^= 1;
        assert!(verify_minisign(&tampered, GOOD_SIG, PUBKEY, "9.9.9").is_err());
        assert!(verify_minisign(b"", GOOD_SIG, PUBKEY, "9.9.9").is_err());
        let mut longer = PACKAGE.to_vec();
        longer.push(b'x');
        assert!(verify_minisign(&longer, GOOD_SIG, PUBKEY, "9.9.9").is_err());
    }

    #[test]
    fn refuses_a_signature_from_another_key() {
        assert!(verify_minisign(PACKAGE, OTHER_KEY_SIG, PUBKEY, "9.9.9").is_err());
        assert!(verify_minisign(PACKAGE, GOOD_SIG, OTHER_PUBKEY, "9.9.9").is_err());
    }

    #[test]
    fn refuses_a_signature_for_another_version_or_with_none() {
        assert!(verify_minisign(PACKAGE, GOOD_SIG, PUBKEY, "9.9.8").is_err());
        assert!(verify_minisign(PACKAGE, OLD_VERSION_SIG, PUBKEY, "9.9.9").is_err());
        assert!(verify_minisign(PACKAGE, NO_VERSION_SIG, PUBKEY, "9.9.9").is_err());
    }

    #[test]
    fn refuses_garbage_signatures_and_keys() {
        for sig in ["", "AAAA", "!!!!", "dW50cnVzdGVkIGNvbW1lbnQ6IHg="] {
            assert!(verify_minisign(PACKAGE, sig, PUBKEY, "9.9.9").is_err(), "sig {sig:?}");
        }
        for key in ["", "REPLACE_WITH_TAURI_UPDATER_PUBKEY", "AAAA"] {
            assert!(verify_minisign(PACKAGE, GOOD_SIG, key, "9.9.9").is_err(), "key {key:?}");
        }
    }

    // ---- daily check ----

    #[test]
    fn quiet_check_runs_at_most_once_a_day() {
        let day = CHECK_INTERVAL_SECS;
        assert!(should_check_now(1_000_000, None));
        assert!(!should_check_now(1_000_000, Some(1_000_000)));
        assert!(!should_check_now(1_000_000 + day - 1, Some(1_000_000)));
        assert!(should_check_now(1_000_000 + day, Some(1_000_000)));
        // a remembered time in the future (clock was set back) does not block checking for good
        assert!(should_check_now(1_000, Some(5_000_000)));
    }

    #[test]
    fn check_cache_round_trips_and_survives_damage() {
        let dir = tempdir("cache");
        let path = dir.join("update-check.json");
        assert_eq!(read_check_cache(&path), None);
        let cache = CheckCache {
            checked_at: 1234,
            available: Some(UpdateInfo { version: "0.2.0".into(), notes: "Fixes".into(), date: None }),
        };
        write_check_cache(&path, &cache).unwrap();
        assert_eq!(read_check_cache(&path), Some(cache));
        std::fs::write(&path, "{not json").unwrap();
        assert_eq!(read_check_cache(&path), None);
        std::fs::write(&path, vec![b' '; 1_000_000]).unwrap();
        assert_eq!(read_check_cache(&path), None);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    // ---- restart and pacman ----

    #[test]
    fn restart_path_loses_the_deleted_suffix() {
        assert_eq!(
            clean_exe_path(Path::new("/usr/bin/magnm-studio (deleted)")),
            PathBuf::from("/usr/bin/magnm-studio")
        );
        assert_eq!(clean_exe_path(Path::new("/usr/bin/magnm-studio")), PathBuf::from("/usr/bin/magnm-studio"));
        assert_eq!(clean_exe_path(Path::new("/opt/a (deleted)/b")), PathBuf::from("/opt/a (deleted)/b"));
    }

    #[test]
    fn pacman_command_is_fixed_and_takes_only_our_package() {
        let good = Path::new("/home/u/.local/share/studio.magnm.desktop/updates/magnm-studio-0.2.0-1-x86_64.pkg.tar.zst");
        let args = pacman_install_args(good).unwrap();
        let text: Vec<String> = args.iter().map(|a| a.to_string_lossy().into_owned()).collect();
        assert_eq!(&text[..4], ["pacman", "-U", "--noconfirm", "--"]);
        assert_eq!(text[4], good.to_string_lossy());
        assert_eq!(text.len(), 5);

        for bad in [
            "relative/magnm-studio-0.2.0-1-x86_64.pkg.tar.zst",
            "/tmp/evil.pkg.tar.zst",
            "/tmp/magnm-studio-0.2.0-1-x86_64.pkg.tar.zst.sh",
            "/tmp/magnm-studio-x-1-x86_64.pkg.tar.zst",
            "/",
            "",
            "-U",
        ] {
            assert!(pacman_install_args(Path::new(bad)).is_err(), "should refuse {bad:?}");
        }
    }

    #[test]
    fn pkexec_failures_read_as_plain_sentences() {
        assert!(describe_pkexec_failure(Some(126)).contains("cancelled"));
        assert!(describe_pkexec_failure(Some(127)).contains("password"));
        assert!(describe_pkexec_failure(Some(1)).contains("could not install"));
        assert!(describe_pkexec_failure(None).contains("could not install"));
    }

    fn tempdir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("magnm-updates-{tag}-{}-{:?}", std::process::id(), std::thread::current().id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }
}
