//! Tauri 2 desktop shell for Magnm Studio.

mod engine;
mod secrets;
mod updates;

use std::fmt::Write as _;
use std::sync::Arc;

use tauri::{AppHandle, Emitter, Manager, RunEvent, State, Url};
use tauri_plugin_notification::NotificationExt;

use engine::EngineSupervisor;
use secrets::SecretStatus;

fn url_encode_component(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    for byte in input.as_bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*byte as char);
            }
            b' ' => out.push_str("%20"),
            other => {
                let _ = write!(&mut out, "%{other:02X}");
            }
        }
    }
    out
}

fn navigate_to_engine(app: &AppHandle, port: u16, token: &str) -> Result<(), String> {
    let url_str = format!("http://127.0.0.1:{port}/?token={token}");
    let url = Url::parse(&url_str).map_err(|err| format!("Invalid engine URL: {err}"))?;
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "Main window not found".to_string())?;
    window
        .navigate(url)
        .map_err(|err| format!("Failed to navigate to engine UI: {err}"))
}

fn navigate_to_error(app: &AppHandle, supervisor: &EngineSupervisor, msg: &str) {
    let base = supervisor.local_base_url();
    let encoded = url_encode_component(msg);
    let url_str = format!("{base}error.html?msg={encoded}");
    if let (Ok(url), Some(window)) = (Url::parse(&url_str), app.get_webview_window("main")) {
        let _ = window.navigate(url);
    }
}

/// Restart the engine on its current port and token. The page stays where it is; the window only
/// navigates when the engine had to move to a new port (or was not running at all).
async fn restart_in_place(
    app: &AppHandle,
    supervisor: &Arc<EngineSupervisor>,
) -> Result<(), String> {
    let sup = Arc::clone(supervisor);
    let result = tauri::async_runtime::spawn_blocking(move || sup.restart_in_place())
        .await
        .map_err(|err| format!("Engine restart task failed: {err}"))?;

    match result {
        Ok(outcome) if outcome.navigate => {
            navigate_to_engine(app, outcome.endpoint.port, &outcome.endpoint.token)
        }
        Ok(_) => Ok(()),
        Err(err) => {
            navigate_to_error(app, supervisor, &err);
            Err(err)
        }
    }
}

/// Return the set/unset status of all known API key names (never returns values).
#[tauri::command]
async fn list_secrets() -> Result<Vec<SecretStatus>, String> {
    tauri::async_runtime::spawn_blocking(secrets::list_secrets_status)
        .await
        .map_err(|err| format!("Failed to list secrets: {err}"))
}

/// Store an allow-listed API key in the OS keychain and restart the engine in place (no navigation).
#[tauri::command]
async fn set_secret(
    app: AppHandle,
    supervisor: State<'_, Arc<EngineSupervisor>>,
    name: String,
    value: String,
) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || secrets::set_secret_value(&name, &value))
        .await
        .map_err(|err| format!("Failed to store secret: {err}"))??;

    restart_in_place(&app, supervisor.inner()).await
}

/// Delete an allow-listed API key from the OS keychain and restart the engine in place (no navigation).
#[tauri::command]
async fn delete_secret(
    app: AppHandle,
    supervisor: State<'_, Arc<EngineSupervisor>>,
    name: String,
) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || secrets::delete_secret_value(&name))
        .await
        .map_err(|err| format!("Failed to delete secret: {err}"))??;

    restart_in_place(&app, supervisor.inner()).await
}

/// One key to store (the value is never logged or echoed in an error).
#[derive(serde::Deserialize)]
struct SecretSet {
    name: String,
    value: String,
}

/// Write and remove several allow-listed keys in the OS keychain WITHOUT restarting the engine. The engine asks for
/// this when keys were added or removed on the phone; it already knows the change and confirms it afterwards.
#[tauri::command]
async fn store_secrets(sets: Vec<SecretSet>, deletes: Vec<String>) -> Result<(), String> {
    let pairs: Vec<(String, String)> = sets.into_iter().map(|s| (s.name, s.value)).collect();
    tauri::async_runtime::spawn_blocking(move || secrets::store_secret_values(&pairs, &deletes))
        .await
        .map_err(|err| format!("Failed to store secrets: {err}"))?
}

/// Restart the local Python engine in place; the window navigates only if the engine moved.
#[tauri::command]
async fn restart_engine(
    app: AppHandle,
    supervisor: State<'_, Arc<EngineSupervisor>>,
) -> Result<(), String> {
    restart_in_place(&app, supervisor.inner()).await
}

/// Return up to the last 30 lines of captured engine stderr output.
#[tauri::command]
fn get_engine_logs(supervisor: State<'_, Arc<EngineSupervisor>>) -> Vec<String> {
    supervisor.recent_error_logs()
}

/// Return true when a real updater public key (not the placeholder) is configured.
pub(crate) fn is_updater_configured(app: &AppHandle) -> bool {
    const PLACEHOLDER: &str = "REPLACE_WITH_TAURI_UPDATER_PUBKEY";
    let Some(updater_cfg) = app.config().plugins.0.get("updater") else {
        return false;
    };
    let Some(pubkey) = updater_cfg.get("pubkey").and_then(|v| v.as_str()) else {
        return false;
    };
    let trimmed = pubkey.trim();
    !trimmed.is_empty() && trimmed != PLACEHOLDER
}

/// Build and run the Tauri desktop application.
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .invoke_handler(tauri::generate_handler![
            list_secrets,
            set_secret,
            delete_secret,
            store_secrets,
            restart_engine,
            get_engine_logs,
            updates::update_status,
            updates::check_update,
            updates::install_update,
            updates::restart_app,
        ])
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;

            let supervisor = Arc::new(EngineSupervisor::new(data_dir));
            if let Some(window) = app.get_webview_window("main") {
                if let Ok(url) = window.url() {
                    let origin = format!("{}://{}/", url.scheme(), url.host_str().unwrap_or("localhost"));
                    supervisor.set_local_base_url(origin);
                }
            }
            app.manage(Arc::clone(&supervisor));
            app.manage(updates::UpdateState::default());

            let handle = app.handle().clone();
            let click_handle = handle.clone();
            let _ = app.notification().on_action(move |performed| {
                if let Some(window) = click_handle.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
                if let Some(route) = performed
                    .notification()
                    .and_then(|n| n.extra().get("route"))
                    .and_then(|v| v.as_str())
                {
                    let _ = click_handle.emit("magnm:notification-click", route.to_string());
                }
            });
            if is_updater_configured(&handle) {
                handle.plugin(tauri_plugin_updater::Builder::new().build())?;
                // A quiet check once a day. It only tells the Settings screen; it never installs anything.
                updates::start_background_checks(handle.clone());
            }

            std::thread::spawn(move || match supervisor.start() {
                Ok((port, token)) => {
                    if let Err(err) = navigate_to_engine(&handle, port, &token) {
                        navigate_to_error(&handle, &supervisor, &err);
                    }
                }
                Err(err) => {
                    navigate_to_error(&handle, &supervisor, &err);
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Magnm Studio");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            if let Some(supervisor) = app_handle.try_state::<Arc<EngineSupervisor>>() {
                supervisor.stop();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::Url;
    use tauri::utils::acl::RemoteUrlPattern;

    #[test]
    fn remote_capability_pattern_matches_ephemeral_loopback_ports() {
        let pattern: RemoteUrlPattern = "http://127.0.0.1:*".parse().unwrap();
        assert!(pattern.test(&Url::parse("http://127.0.0.1:54321/?token=abc").unwrap()));
        assert!(pattern.test(&Url::parse("http://127.0.0.1:8000/api/status").unwrap()));
        assert!(!pattern.test(&Url::parse("https://127.0.0.1:54321/").unwrap()));
        assert!(!pattern.test(&Url::parse("http://example.com:54321/").unwrap()));
    }
}

